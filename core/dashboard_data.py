"""
SurplusIQ — Dashboard Data Exporter

Generates two JSON files that the dashboard HTML reads:

  docs/data/leads.json      — all qualifying leads
  docs/data/summary.json    — county totals, score distribution, KPIs

Usage:
    python -m core.dashboard_data
"""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

from core.loader import load_all_leads, get_summary, PROJECT_ROOT


def export_dashboard_data():
    docs_data = PROJECT_ROOT / "docs" / "data"
    docs_data.mkdir(parents=True, exist_ok=True)

    print("📊 Loading leads...")
    leads = load_all_leads()
    summary = get_summary(leads)
    print(f"   ✓ {len(leads)} leads loaded")
    print(f"   ✓ ${summary['total_surplus']:,.0f} total surplus")

    # Write leads.json — array of lead dicts (slim version, no PII / debug fields)
    leads_payload = []
    for l in leads:
        leads_payload.append({
            "county_id":        l.county_id,
            "county_name":      l.county_name,
            "state":            l.state,
            "case_number":      l.case_number,
            "address":          l.address,
            "parcel_id":        l.parcel_id,
            "auction_type":     l.auction_type,
            "opening_bid":      l.opening_bid,
            "final_sale_price": l.final_sale_price,
            "gross_surplus":    l.gross_surplus,
            "assessed_value":   l.assessed_value,
            "sale_date":        l.sale_date,
            "sold_to":          l.sold_to,
            "auction_status":   l.auction_status,
            "score":            l.score,
        })

    leads_file = docs_data / "leads.json"
    with open(leads_file, "w") as f:
        json.dump(leads_payload, f, indent=2)
    print(f"   ✓ Wrote {leads_file.relative_to(PROJECT_ROOT)}")

    # Write summary.json — KPIs, county breakdown, score distribution
    summary_file = docs_data / "summary.json"
    summary_payload = {
        "generated_at":   summary["generated_at"],
        "total_leads":    summary["total_leads"],
        "total_surplus":  summary["total_surplus"],
        "by_state":       summary["by_state"],
        "by_county":      summary["by_county"],
        "by_score":       summary["by_score"],
        "top_5_leads":    summary["top_5_leads"],
        "coverage": {
            "states":   ["FL", "OH"],
            "counties": [
                {"id": "miami-dade-fl", "name": "Miami-Dade", "state": "FL"},
                {"id": "broward-fl",    "name": "Broward",    "state": "FL"},
                {"id": "duval-fl",      "name": "Duval",      "state": "FL"},
                {"id": "lee-fl",        "name": "Lee",        "state": "FL"},
                {"id": "orange-fl",     "name": "Orange",     "state": "FL"},
                {"id": "cuyahoga-oh",   "name": "Cuyahoga",   "state": "OH"},
                {"id": "franklin-oh",   "name": "Franklin",   "state": "OH"},
                {"id": "montgomery-oh", "name": "Montgomery", "state": "OH"},
                {"id": "summit-oh",     "name": "Summit",     "state": "OH"},
                {"id": "hamilton-oh",   "name": "Hamilton",   "state": "OH"},
            ],
        },
    }

    with open(summary_file, "w") as f:
        json.dump(summary_payload, f, indent=2)
    print(f"   ✓ Wrote {summary_file.relative_to(PROJECT_ROOT)}")

    return leads_file, summary_file


if __name__ == "__main__":
    leads_file, summary_file = export_dashboard_data()
    print()
    print("=" * 70)
    print("  ✓ Dashboard data export complete")
    print(f"  📁 {leads_file}")
    print(f"  📁 {summary_file}")
    print("=" * 70)
