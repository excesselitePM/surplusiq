"""
SurplusIQ — Date Mismatch Verifier (v2 — fixed field name)

The scraper writes the URL date into `auction_date`, NOT `sale_date`.
v1 of this script checked the wrong field and reported 0/0 incorrectly.
v2 reads `auction_date` (the URL date) and compares against ALL dates
found in `raw_text` to find mismatches.
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

COUNTIES = [
    "miami-dade-fl", "broward-fl", "duval-fl", "lee-fl", "orange-fl",
    "cuyahoga-oh", "franklin-oh", "montgomery-oh", "summit-oh", "hamilton-oh",
]


def find_dates_in_text(text: str) -> list[str]:
    """Find all M/D/YYYY style dates in the raw text. Returns ISO strings."""
    if not text:
        return []
    found = set()
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        try:
            mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            d = datetime(yyyy, mm, dd).date()
            found.add(d.isoformat())
        except ValueError:
            continue
    return sorted(found)


def latest_jsonl(county_id: str) -> Path | None:
    files = sorted(p for p in RAW_DIR.glob(f"{county_id}_*.jsonl") if p.stat().st_size > 0)
    return files[-1] if files else None


def verify_county(county_id: str) -> dict:
    path = latest_jsonl(county_id)
    if not path:
        return {"county": county_id, "error": "no raw file"}

    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    total = len(records)
    matches = 0
    mismatches = 0
    no_date_in_text = 0
    examples_mismatch = []
    text_year_distribution = defaultdict(int)
    url_year_distribution = defaultdict(int)

    for rec in records:
        # The auction_date the scraper recorded (from URL parameter)
        recorded = (rec.get("auction_date") or rec.get("sale_date") or "").strip()[:10]
        if not recorded:
            continue

        try:
            recorded_d = datetime.fromisoformat(recorded).date()
        except ValueError:
            continue

        url_year_distribution[recorded[:4]] += 1

        # All dates found in the raw text (the actual sale dates)
        text = rec.get("raw_text", "") or ""
        dates_in_text = find_dates_in_text(text)

        if not dates_in_text:
            no_date_in_text += 1
            continue

        # The first date in the raw_text is typically the actual sale date
        # (Auction Sold\n<DATE> format)
        actual_date = dates_in_text[0] if dates_in_text else None

        # Track text years
        if actual_date:
            text_year_distribution[actual_date[:4]] += 1

        # If the URL date matches the first date in text, it's a match
        if recorded == actual_date:
            matches += 1
        else:
            mismatches += 1
            if len(examples_mismatch) < 5:
                examples_mismatch.append({
                    "case": rec.get("case_number", "?"),
                    "url_date": recorded,
                    "actual_in_text": actual_date,
                    "all_text_dates": dates_in_text[:5],
                    "address": (rec.get("address", "") or "").replace("\t", " ").replace("\n", " ")[:60],
                })

    return {
        "county": county_id,
        "file": path.name,
        "total": total,
        "matches": matches,
        "mismatches": mismatches,
        "no_date_in_text": no_date_in_text,
        "url_year_distribution": dict(url_year_distribution),
        "text_year_distribution": dict(text_year_distribution),
        "examples_mismatch": examples_mismatch,
    }


def main():
    print()
    print("=" * 78)
    print("  SurplusIQ — Date Mismatch Verifier (v2)")
    print("=" * 78)
    print()
    print("Comparing scraper-recorded auction_date (from URL) against the actual")
    print("sale date found in the item's raw text. Mismatches = the bug Eric flagged.")
    print()

    grand = {"total": 0, "matches": 0, "mismatches": 0, "no_date": 0}

    for county_id in COUNTIES:
        r = verify_county(county_id)
        if "error" in r:
            print(f"⚠  {county_id:<18}  {r['error']}")
            continue

        total = r["total"]
        match = r["matches"]
        mismatch = r["mismatches"]
        nodate = r["no_date_in_text"]

        grand["total"] += total
        grand["matches"] += match
        grand["mismatches"] += mismatch
        grand["no_date"] += nodate

        if mismatch == 0:
            flag = "✅"
        elif mismatch / max(total, 1) > 0.3:
            flag = "🚨"
        else:
            flag = "⚠️ "

        print(f"{flag} {county_id:<18}  {total:>3} records  |  matches: {match:>3}  |  MISMATCHES: {mismatch:>3}  |  no-date-in-text: {nodate:>3}")

        # Show year distribution comparison
        if r["text_year_distribution"]:
            text_years = ", ".join(f"{y}: {n}" for y, n in sorted(r["text_year_distribution"].items()))
            print(f"    Years from RAW TEXT (truth): {text_years}")
        if r["url_year_distribution"]:
            url_years = ", ".join(f"{y}: {n}" for y, n in sorted(r["url_year_distribution"].items()))
            print(f"    Years from URL (recorded):   {url_years}")

        if r["examples_mismatch"]:
            print(f"    🔎 Sample mismatches (URL date vs actual text date):")
            for ex in r["examples_mismatch"]:
                print(f"      • case {ex['case']}")
                print(f"          URL said:    {ex['url_date']}")
                print(f"          text says:   {ex['actual_in_text']}")
                if ex['address']:
                    print(f"          address:     {ex['address']}")
        print()

    print("=" * 78)
    print(f"  TOTAL: {grand['total']} records  |  matches: {grand['matches']}  |  "
          f"MISMATCHES: {grand['mismatches']}  |  no-date-in-text: {grand['no_date']}")
    print("=" * 78)
    print()
    if grand["mismatches"] > 0:
        pct = grand["mismatches"] / max(grand["total"], 1) * 100
        print(f"  🚨 {pct:.1f}% of records have wrong dates baked in.")
        print(f"  The scraper needs the date-match guard fix (Brittany's pattern).")
    else:
        print("  ✅ All recorded dates match the source text. No fix needed.")
    print()


if __name__ == "__main__":
    main()
