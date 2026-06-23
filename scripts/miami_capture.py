"""
Miami-Dade — docket HTML CAPTURE for the acceptance-test fixtures (throwaway).

NOT production. Reuses the PROVEN MiamiDadeDocketScraper.fetch_docket_html (React
SPA + reCAPTCHA v3 passes headless from the Actions datacenter IP) to capture the
real Case-Information HTML for a fixed list of cases, so tests/test_miami_dade_docket.py
can run network-free against REAL committed docket data (same rigor as Broward/Duval).

Also prints parse_docket + _apply_evidence_level verdict per case so the Actions
log shows the kill path firing on the real claim/sale-issue/bankruptcy cases.

Saves to data/samples/miami_dade/cases/<tag>.html.
Usage: python scripts/miami_capture.py "2024-020538-CA-01,2025-000672-CA-01,..."
"""
import sys, os, re, asyncio
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.dockets.base import DocketResult
from core.dockets.miami_dade import MiamiDadeDocketScraper

OUT = Path("data/samples/miami_dade/cases")

DEFAULT_CASES = [
    "2024-020538-CA-01",   # real surplus claim → killed (Owners Claim for ... Surplus)
    "2025-000672-CA-01",   # sale cancelled (+ bankruptcy) → killed (sale_issue ranks above bk)
    "2020-000151-CA-01",   # cancelled sale → killed (sale_issue)
    "2023-001008-CA-01",   # cancelled sale → killed (sale_issue)
    "2017-021344-CA-01",   # bankruptcy later resolved (stay lifted/reopen) → caution, NOT killed
    "2019-009163-CC-05",   # clean → pursuable
    "2025-095651-CC-25",   # clean → pursuable
]


def tag(cn: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", cn).upper()


async def main():
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    cases = [c.strip() for c in arg.split(",") if c.strip()] if arg else DEFAULT_CASES
    OUT.mkdir(parents=True, exist_ok=True)
    s = MiamiDadeDocketScraper()
    print(f"capturing {len(cases)} Miami-Dade cases\n")
    for cn in cases:
        print(f"===== {cn} =====")
        try:
            fetched = await s.fetch_docket_html(cn)
        except Exception as e:
            print(f"  fetch EXC: {type(e).__name__}: {str(e)[:140]}")
            continue
        if not fetched.get("ok"):
            print(f"  fetch FAILED: {fetched.get('error','')[:140]}")
            continue
        html = fetched["html"]
        (OUT / f"{tag(cn)}.html").write_text(html, encoding="utf-8")
        # run the real Phase-2 path and print the verdict
        r = DocketResult(county_id="miami-dade-fl", case_number=cn)
        s.parse_docket(html, r)
        s._apply_evidence_level(r)
        titles = s._docket_titles(html)
        print(f"  saved {len(html)} bytes | {len(titles)} docket titles")
        print(f"  classification: {r.classification} / {r.lead_status} / {r.evidence_level}")
        print(f"  reason: {r.classification_reason[:150]}")


if __name__ == "__main__":
    asyncio.run(main())
