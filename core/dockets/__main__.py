"""
Single-case docket entrypoint (the interface advertised in __init__'s docstring).

    python -m core.dockets <county-id> <case-number> [--headed]

Scrapes ONE case live and prints the full DocketResult as JSON. Used to verify a
county scraper end-to-end on a specific known case (e.g. reproduce a kill on the
live site in CI). Per CLAUDE.md this is GitHub-Actions-only for real scrapers —
do not run locally against a live clerk site.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys

from . import get_scraper, SCRAPER_REGISTRY


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m core.dockets")
    ap.add_argument("county_id", help=f"one of: {', '.join(SCRAPER_REGISTRY)}")
    ap.add_argument("case_number")
    ap.add_argument("--headed", action="store_true", help="show browser (default headless)")
    args = ap.parse_args()

    if args.county_id not in SCRAPER_REGISTRY:
        print(f"No docket scraper for {args.county_id}. Available: {list(SCRAPER_REGISTRY)}")
        return 2

    scraper = get_scraper(args.county_id, headless=not args.headed)
    result = asyncio.run(scraper.scrape_case(args.case_number))
    d = result.to_dict()
    print(json.dumps(d, indent=2, default=str))
    print(f"\n>>> {args.case_number}: {d.get('classification', '?').upper()} "
          f"/ {d.get('lead_status', '')} — {d.get('classification_reason', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
