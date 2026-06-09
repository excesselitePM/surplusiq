"""Phase 1 verification: does miami_dade.fetch_docket_html return the full
docket HTML, headless, for real Miami-Dade case numbers?

Run:  python data/samples/miami_dade/_test_phase1.py
"""
import asyncio
import re
import sys

from core.dockets.miami_dade import (
    MiamiDadeDocketScraper,
    parse_miami_dade_case_number,
)

# Investigation case + real cases from docs/data/leads.json (mix of CA/CC
# mortgage-foreclosure + one tax-deed to confirm the type guard).
CASES = [
    "2017-021344-CA-01",   # investigation baseline (WILMINGTON TRUST)
    "2025-012741-CA-01",   # leads.json, CA
    "2019-009163-CC-05",   # leads.json, CC, location 05
    "2025-095651-CC-25",   # leads.json, CC, location 25
    "2026A00137",          # leads.json TAX DEED — must be detected, not searched
]


def docket_signal(html: str) -> dict:
    txt = re.sub(r"<[^>]+>", " ", html)
    return {
        "Docket": txt.count("Docket"),
        "Motion": txt.count("Motion"),
        "FinalJudgment": txt.lower().count("final judgment"),
        "Bankruptcy": txt.count("Bankruptcy"),
        "vs": " vs " in txt or " vs. " in txt,
    }


async def main():
    scraper = MiamiDadeDocketScraper(headless=True)
    rows = []
    for cn in CASES:
        parsed = parse_miami_dade_case_number(cn)
        ftype = parsed.get("foreclosure_type") if parsed else "UNPARSEABLE"
        print(f"\n===== {cn}  (type={ftype}) =====")
        res = await scraper.fetch_docket_html(cn)
        if res["ok"]:
            sig = docket_signal(res["html"])
            # pull case caption for human confirmation
            m = re.search(r"Case Details\s*</[^>]+>\s*<[^>]+>([^<]{5,120})", res["html"])
            cap = ""
            t = re.sub(r"<[^>]+>", " ", res["html"])
            cm = re.search(r"([A-Z][A-Za-z .,&]+ vs\.? [A-Z][A-Za-z .,&]+)", t)
            if cm:
                cap = cm.group(1).strip()[:80]
            print(f"  OK  url={res['url'][:80]}")
            print(f"      html_bytes={len(res['html'])}  caption={cap!r}")
            print(f"      signals={sig}")
            rows.append((cn, ftype, "OK", len(res["html"]), sig, cap))
        else:
            print(f"  FAIL  error={res['error']}")
            rows.append((cn, ftype, f"FAIL: {res['error']}", 0, {}, ""))

    print("\n\n================ SUMMARY ================")
    for cn, ftype, status, nbytes, sig, cap in rows:
        print(f"{cn:20} {ftype:20} {status[:60]}")
    # Acceptance: all mortgage_foreclosure cases OK, tax_deed correctly skipped
    mort = [r for r in rows if r[1] == "mortgage_foreclosure"]
    ok_mort = [r for r in mort if r[2] == "OK"]
    td = [r for r in rows if r[1] == "tax_deed"]
    print(f"\nmortgage-foreclosure OK: {len(ok_mort)}/{len(mort)}")
    print(f"tax-deed correctly detected (not searched): "
          f"{sum(1 for r in td if r[2].startswith('FAIL: tax_deed'))}/{len(td)}")


if __name__ == "__main__":
    asyncio.run(main())
