"""
SurplusIQ — Docket Enrichment CLI

Reads current leads from data/raw/<county>_<date>.jsonl, runs the docket
scraper against each one, and saves enriched results to data/dockets/.

Usage:
  python -m core.dockets.enrich cuyahoga-oh                # all Cuyahoga leads
  python -m core.dockets.enrich cuyahoga-oh --case CV25110711
  python -m core.dockets.enrich cuyahoga-oh --headed       # see browser
"""

from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

# Project paths
def _find_project_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "config" / "counties.py").exists():
            return parent
    return Path(__file__).resolve().parent.parent.parent

PROJECT_ROOT = _find_project_root()
RAW_DIR = PROJECT_ROOT / "data" / "raw"
DOCKETS_DIR = PROJECT_ROOT / "data" / "dockets"
DOCKETS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(PROJECT_ROOT))
from core.dockets import get_scraper, SCRAPER_REGISTRY


def latest_raw_file(county_id: str) -> Path | None:
    files = sorted(p for p in RAW_DIR.glob(f"{county_id}_*.jsonl") if p.stat().st_size > 0)
    return files[-1] if files else None


def load_cases_from_raw(county_id: str) -> list[dict]:
    f = latest_raw_file(county_id)
    if not f:
        return []
    records = []
    with open(f) as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _case_key_for_county(county_id: str):
    """Return a callable(raw_case_number)->normalized_case_key for grouping
    parcels under the same clerk case. Falls back to identity when no
    parser is registered. Used for multi-parcel blanket-judgment
    aggregation (see run_county docstring)."""
    try:
        if county_id == "summit-oh":
            from core.dockets.summit import parse_summit_case_number
            return lambda raw: (parse_summit_case_number(raw) or {}).get("joined") or raw
        if county_id == "montgomery-oh":
            from core.dockets.montgomery import parse_montgomery_case_number
            return lambda raw: (parse_montgomery_case_number(raw) or {}).get("search_text") or raw
        if county_id == "cuyahoga-oh":
            from core.dockets.cuyahoga import parse_cuyahoga_case_number
            def _cuy_key(raw):
                p = parse_cuyahoga_case_number(raw) or {}
                if p:
                    return f"{p.get('case_prefix','CV')}-{p.get('year','????')}-{p.get('number','')}"
                return raw
            return _cuy_key
    except Exception:
        pass
    return lambda raw: raw


async def run_one(county_id: str, case_number: str, headless: bool, final_sale_price: float = 0.0) -> dict:
    scraper = get_scraper(county_id, headless=headless)
    result = await scraper.scrape_case(case_number)

    # Re-classify with the actual sale price if provided
    if final_sale_price > 0:
        result.classification, result.classification_reason = scraper.classify(result, final_sale_price)

    return result.to_dict()


async def run_county(county_id: str, headless: bool, only_case: str | None = None) -> dict:
    # Cloudflare-blocked counties: no docket automation. These are Eric-approved
    # PR-fallback counties — the docket step skips them and PropertyRadar
    # enrichment + the dashboard's manual-verify clerk link carry the load.
    PR_FALLBACK_COUNTIES = {"franklin-oh", "hamilton-oh"}
    if county_id in PR_FALLBACK_COUNTIES:
        print(f"⏭  {county_id} is Cloudflare-blocked. Skipping docket scrape — "
              f"this county is routed through PropertyRadar enrichment + the "
              f"dashboard's manual-verify clerk link.")
        return {"county_id": county_id, "results": [], "skipped": True,
                "reason": "PR-fallback county (Cloudflare block)"}

    if county_id not in SCRAPER_REGISTRY:
        raise SystemExit(f"No docket scraper for {county_id}. Available: {list(SCRAPER_REGISTRY.keys())}")

    records = load_cases_from_raw(county_id)
    if not records:
        raise SystemExit(f"No raw scraper data found for {county_id}. Run the auction scraper first.")

    if only_case:
        records = [r for r in records if r.get("case_number", "").startswith(only_case[:8])]

    # ─── Group parcels by clerk-case-key for blanket-judgment aggregation ─
    # Multiple auction parcels can share ONE foreclosure judgment (e.g.
    # Summit CV-2025-02-0548 secured 12 parcels jointly and severally for
    # one $852K judgment). Per-parcel `true_surplus = sale - debt` wrongly
    # KILLS each parcel even when sum-of-sales clears the debt. Group by
    # case_key first, scrape the docket once per group, then compare the
    # AGGREGATE sale across the group to the single prayer amount.
    case_key_fn = _case_key_for_county(county_id)
    groups: dict[str, list[dict]] = {}
    for rec in records:
        key = case_key_fn(rec.get("case_number", "")) or rec.get("case_number", "")
        groups.setdefault(key, []).append(rec)

    multi_groups = [k for k, v in groups.items() if len(v) > 1]
    print(f"\n🏛  {county_id} docket scrape — {len(records)} parcels in {len(groups)} cases "
          f"({len(multi_groups)} multi-parcel groups)")
    print(f"    Headless: {headless}")
    print()

    results = []
    group_idx = 0
    for case_key, parcels in groups.items():
        group_idx += 1
        # Scrape using the first parcel's case number; the clerk lookup is
        # identical for every parcel in the group.
        rep = parcels[0]
        rep_case = rep.get("case_number", "")
        aggregate_sale = sum(float(p.get("final_sale_price") or 0.0) for p in parcels)
        aggregate_apparent = sum(float(p.get("gross_surplus") or 0.0) for p in parcels)

        if len(parcels) > 1:
            print(f"  [{group_idx}/{len(groups)}] {case_key}  ⛓ BLANKET-JUDGMENT GROUP "
                  f"({len(parcels)} parcels, aggregate sale ${aggregate_sale:,.0f})")
        else:
            print(f"  [{group_idx}/{len(groups)}] {rep_case}  "
                  f"(apparent surplus ${aggregate_apparent:,.0f})")

        # Scrape with aggregate_sale so classify() compares against pooled
        # proceeds, not just one parcel's sale.
        try:
            result = await run_one(
                county_id, rep_case,
                headless=headless,
                final_sale_price=aggregate_sale,
            )
        except Exception as e:
            print(f"      ⚠ scrape failed: {e}")
            continue

        prayer = result.get("prayer_amount", 0.0)
        # Aggregate true_surplus: pooled sale across the case minus the
        # single judgment amount. For non-multi-parcel cases this collapses
        # back to the old (final - prayer) math.
        true_surplus = aggregate_sale - prayer if prayer > 0 else None
        cls = result.get("classification", "?")
        reason = result.get("classification_reason", "")

        if prayer > 0:
            if len(parcels) > 1:
                print(f"      prayer amount: ${prayer:,.0f}   |   aggregate true surplus: ${true_surplus:,.0f}  "
                      f"(over {len(parcels)} parcels)")
            else:
                print(f"      prayer amount: ${prayer:,.0f}   |   true surplus: ${true_surplus:,.0f}")
        else:
            print(f"      prayer amount: not found")
        print(f"      classification: {cls.upper()}  ({reason})")
        if result.get("kill_signals"):
            print(f"      🚨 kill signals: {', '.join(result['kill_signals'])}")
        if result.get("proof_of_surplus"):
            print(f"      ✅ proof: {result['proof_of_surplus']}")
        if result.get("competing_filers"):
            print(f"      ⚠️  competing: {', '.join(result['competing_filers'])}")
        print()

        # Emit ONE enriched record per ORIGINAL parcel, all sharing the same
        # docket result but each carrying its own auction-side data. This
        # keeps the dashboard's per-parcel rendering intact while the
        # classification (KILLED/RED/YELLOW/GREEN) honors the aggregated math.
        for parcel in parcels:
            parcel_record = dict(result)
            parcel_record["case_number"] = parcel.get("case_number", "")
            parcel_record["_auction_data"] = {
                "final_sale_price": float(parcel.get("final_sale_price") or 0.0),
                "opening_bid":      float(parcel.get("opening_bid") or 0.0),
                "apparent_surplus": float(parcel.get("gross_surplus") or 0.0),
                "true_surplus":     true_surplus,
                "address":          parcel.get("address", ""),
            }
            parcel_record["_blanket_group"] = {
                "case_key":             case_key,
                "parcel_count":         len(parcels),
                "aggregate_sale":       aggregate_sale,
                "aggregate_apparent":   aggregate_apparent,
            }
            results.append(parcel_record)

    # Save enriched results
    out_file = DOCKETS_DIR / f"{county_id}_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    with open(out_file, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print(f"\n💾 Saved {len(results)} docket results to {out_file.relative_to(PROJECT_ROOT)}")

    # Summary
    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)
    by_class = {}
    for r in results:
        c = r.get("classification", "unknown")
        by_class[c] = by_class.get(c, 0) + 1
    for c in ["green", "yellow", "red", "killed", "unknown"]:
        if by_class.get(c):
            print(f"  {c.upper():<8}  {by_class[c]:>3} leads")
    print()

    return {"county_id": county_id, "results": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("county_id", help="County identifier, e.g. cuyahoga-oh")
    ap.add_argument("--case", help="Run against one specific case number prefix only")
    ap.add_argument("--headed", action="store_true", help="Show browser window (default: headless)")
    args = ap.parse_args()

    asyncio.run(run_county(args.county_id, headless=not args.headed, only_case=args.case))


if __name__ == "__main__":
    main()
