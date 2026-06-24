"""
THROWAWAY investigation script — pull real Summit judgment PDFs by EXACT case
number, bypassing load_cases_from_raw (which only reads the latest auction raw,
so older client-walked cases like 6973 Van Buren / 375 Revere aren't reachable
via `enrich --case`). Calls the EXISTING SummitDocketScraper.scrape_case()
unchanged — no extraction-logic change. The scraper saves the judgment PDF +
full pdfplumber text to data/diagnostics/summit-oh/ on success.

Usage: python scripts/summit_pull_judgments.py CV2024125264,CV2025052012,...
"""
import sys
import asyncio
from pathlib import Path

# Run-as-script: put the repo root on sys.path so `core` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.dockets.summit import SummitDocketScraper

# Client-walked + variety cases (case → address/why):
#   CV2024125264  6973 VAN BUREN ROAD   sale $277,300, opening $238,000 — the
#                                       false-surplus case (principal ~$244-248K)
#   CV2025052012  375 REVERE ROAD       sale $258,100 — the "Revere" case
#   CV2025115614  3234 SALMON DRIVE     sale $136,800 — FP-11 rescued YELLOW
#   CV2025105047  734 CANTON ROAD       sale $188,700 — large apparent surplus
DEFAULT_CASES = [
    "CV2024125264", "CV2025052012", "CV2025115614", "CV2025105047",
]


async def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    cases = [c.strip() for c in arg.split(",") if c.strip()] or DEFAULT_CASES
    scraper = SummitDocketScraper(headless=True)
    for cn in cases:
        print(f"\n================= SUMMIT scrape_case {cn} =================")
        try:
            result = await scraper.scrape_case(cn)
            print(f"  → case={cn} prayer=${result.prayer_amount:,.2f} "
                  f"class={result.classification} debt_source={result.debt_source!r}")
        except Exception as e:
            print(f"  ❌ {cn}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
