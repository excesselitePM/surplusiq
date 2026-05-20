"""
SurplusIQ — Unified Data Loader (v2 — 14-day cutoff enforced)

Consolidates raw scraped data from all 10 counties into a single clean dataset
ready for Excel export, dashboard rendering, and PropertyRadar enrichment.

CHANGES IN v2:
  • Hard 14-day window: any lead with sale_date older than (today - 14 days)
    is dropped before reaching the dashboard / Excel / enrichment.
  • If sale_date can't be parsed, the lead is dropped as well.
  • Console output reports how many were dropped and why, so we can verify
    the filter is doing what we expect each time.

Usage:
    from core.loader import load_all_leads, get_summary

    leads = load_all_leads()                    # all qualifying leads (last 14 days)
    leads = load_all_leads(min_surplus=25000)   # higher surplus threshold
    leads = load_all_leads(window_days=7)       # tighter date window
    summary = get_summary(leads)                # county totals
"""

from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field, asdict, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# Project paths
# ═══════════════════════════════════════════════════════════════════════
def _find_project_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "config" / "counties.py").exists():
            return parent
    return Path(__file__).resolve().parent.parent

PROJECT_ROOT = _find_project_root()
RAW_DIR      = PROJECT_ROOT / "data" / "raw"


# ═══════════════════════════════════════════════════════════════════════
# County metadata (ID → display info)
# ═══════════════════════════════════════════════════════════════════════
COUNTY_INFO = {
    "miami-dade-fl": {"name": "Miami-Dade", "state": "FL", "platform": "Florida — RealForeclose"},
    "broward-fl":    {"name": "Broward",    "state": "FL", "platform": "Florida — RealForeclose"},
    "duval-fl":      {"name": "Duval",      "state": "FL", "platform": "Florida — RealForeclose (Tax Deed)"},
    "lee-fl":        {"name": "Lee",        "state": "FL", "platform": "Florida — RealForeclose"},
    "orange-fl":     {"name": "Orange",     "state": "FL", "platform": "Florida — RealForeclose"},
    "cuyahoga-oh":   {"name": "Cuyahoga",   "state": "OH", "platform": "Ohio — SheriffSaleAuction"},
    "franklin-oh":   {"name": "Franklin",   "state": "OH", "platform": "Ohio — SheriffSaleAuction"},
    "montgomery-oh": {"name": "Montgomery", "state": "OH", "platform": "Ohio — SheriffSaleAuction"},
    "summit-oh":     {"name": "Summit",     "state": "OH", "platform": "Ohio — SheriffSaleAuction"},
    "hamilton-oh":   {"name": "Hamilton",   "state": "OH", "platform": "Ohio — SheriffSaleAuction"},
}


# ═══════════════════════════════════════════════════════════════════════
# Lead data structure
# ═══════════════════════════════════════════════════════════════════════


def _load_docket_data() -> dict:
    """
    Load all docket scraper results from data/dockets/ into a lookup dict.
    Returns: { (county_id, normalized_case_number): docket_result_dict }
    """
    import json as _json
    docket_dir = PROJECT_ROOT / "data" / "dockets"
    if not docket_dir.exists():
        return {}

    lookup = {}
    # Walk every .jsonl file in data/dockets/
    for jsonl in sorted(docket_dir.glob("*.jsonl")):
        try:
            with open(jsonl) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    cid = d.get("county_id", "")
                    case = d.get("case_number", "")
                    if not cid or not case:
                        continue
                    # Normalize: strip "(NNNNN)" auction suffix and whitespace
                    norm = re.sub(r"\s*\([^)]*\)\s*$", "", case).strip().upper()
                    lookup[(cid, norm)] = d
        except Exception:
            continue
    return lookup


def _normalize_case_for_lookup(case_number: str) -> str:
    """Strip the auction suffix '(NNNNN)' from case numbers for matching."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", case_number).strip().upper()


def _apply_docket_to_lead(lead, docket: dict, county_id: str) -> None:
    """
    Merge a docket result onto a Lead in place.

    HARDENING (FP-1/FP-2/FP-3): true_surplus is now a DOCKET-ONLY field.
    It is set ONLY when the docket supplies a real debt figure (prayer/writ/
    judgment amount). It is NEVER defaulted to gross_surplus.

      • Ohio   — opening_bid is fake (2/3 appraised value, statutory). The only
                 valid debt is the docket prayer amount. No prayer amount =>
                 true_surplus stays None (lead is apparent-only).
      • Florida — opening_bid equals the judgment, but auction math alone is
                 still not a confirmed surplus. true_surplus is only set here
                 if the docket itself supplies a debt figure. Otherwise None.

    A None true_surplus is the explicit signal that this lead has NOT been
    docket-verified and must not be treated as confirmed surplus downstream.
    """
    lead.classification       = docket.get("classification", "") or ""
    lead.classification_reason = docket.get("classification_reason", "") or ""
    lead.prayer_amount        = float(docket.get("prayer_amount", 0.0) or 0.0)
    lead.kill_signals         = list(docket.get("kill_signals", []) or [])
    lead.proof_of_surplus     = docket.get("proof_of_surplus", "") or ""
    lead.competing_filers     = list(docket.get("competing_filers", []) or [])
    lead.additional_parties   = list(docket.get("additional_parties", []) or [])
    lead.docket_url           = docket.get("case_url", "") or ""

    # true_surplus = final_sale_price - real_debt, where real_debt comes ONLY
    # from the docket. No docket debt figure => true_surplus stays None.
    if lead.prayer_amount > 0:
        lead.true_surplus = round(lead.final_sale_price - lead.prayer_amount, 2)
    else:
        lead.true_surplus = None



@dataclass
class Lead:
    # Identity
    county_id:      str
    county_name:    str
    state:          str
    case_number:    str

    # Property
    address:        str
    parcel_id:      str
    auction_type:   str

    # Financials
    opening_bid:    float
    final_sale_price: float
    gross_surplus:  float
    assessed_value: float

    # Sale details
    sale_date:      str         # ISO format (YYYY-MM-DD) after normalization
    sale_datetime:  str         # Full readable timestamp e.g. "May 4, 2026 9:02 AM ET"
    sold_to:        str
    is_third_party: bool
    source_url:     str         # Direct link to the county auction page


    # Lead quality
    auction_status: str

    # Source
    scraped_at:     str
    source_file:    str

    # Lead score
    score:          str = ""
    score_reason:   str = ""

    # Enrichment placeholders
    enriched:           bool   = False
    estimated_value:    float  = 0.0
    mortgage_balance:   float  = 0.0
    secondary_liens:    float  = 0.0
    net_surplus:        float  = 0.0
    owner_name:         str    = ""
    owner_address:      str    = ""

    # Claim status
    claim_filed:        bool   = False
    claim_status:       str    = "Unknown"

    # Docket-enrichment fields (populated when docket scraper has run on this case)
    classification:   str = ""
    classification_reason: str = ""
    prayer_amount:    float = 0.0
    true_surplus:     Optional[float] = None   # None = NOT docket-verified
    kill_signals:     list = field(default_factory=list)
    proof_of_surplus: str = ""
    competing_filers: list = field(default_factory=list)
    additional_parties: list = field(default_factory=list)
    docket_url:       str = ""

    # Verification status model (HARDENING — assigned by assign_status_fields)
    research_status:  str = "unknown"
    lead_quality:     str = "unknown"
    money_status:     str = "unknown"
    evidence_level:   str = "unknown"
    pipeline_ready:   bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════
def _latest_jsonl_for_county(county_id: str) -> Optional[Path]:
    pattern = f"{county_id}_*.jsonl"
    files = sorted(RAW_DIR.glob(pattern))
    return files[-1] if files else None


def _extract_sale_datetime(record: dict) -> str:
    """
    Extract a human-readable timestamp like "May 4, 2026 9:02 AM ET" from the raw_text.
    Returns empty string if not parseable.
    """
    raw = record.get("raw_text", "") or ""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}:\d{2})\s*(AM|PM)?\s*ET", raw, re.IGNORECASE)
    if not m:
        return ""
    try:
        mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        time_str = m.group(4)
        ampm = (m.group(5) or "").upper()
        d = date(yyyy, mm, dd)
        return f"{d.strftime('%b %-d, %Y')} {time_str} {ampm} ET".strip()
    except (ValueError, AttributeError):
        return ""


def _normalize_address(raw: str) -> str:
    if not raw:
        return ""
    return raw.replace("Property Address:", "").strip()


def _extract_sale_date(record: dict) -> Optional[date]:
    """
    Try every plausible source for the sale date and return a date object.
    Returns None if no parseable date is found.
    """
    # Direct fields first
    for key in ("sale_date", "sale_datetime", "auction_date", "soldDate", "AUCTIONDATE"):
        v = record.get(key)
        if v:
            iso = str(v)[:10]
            try:
                return date.fromisoformat(iso)
            except ValueError:
                pass

    # Pull from raw_text — most scrapers store the unparsed page text
    raw_text = record.get("raw_text", "") or ""

    patterns = [
        r"(\d{1,2}/\d{1,2}/\d{4})\s+\d{1,2}:\d{2}",
        r"Sold on\s+(\d{1,2}/\d{1,2}/\d{4})",
        r"Sale Date[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
        r"AUCTIONDATE[=:\s]+(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{1,2}/\d{1,2}/\d{4})",
    ]
    for pat in patterns:
        m = re.search(pat, raw_text)
        if m:
            try:
                return datetime.strptime(m.group(1), "%m/%d/%Y").date()
            except ValueError:
                continue
    return None


def _parse_lead(record: dict, county_id: str, source_file: str) -> Optional[Lead]:
    """Convert a raw scraper record into a Lead dataclass."""
    info = COUNTY_INFO.get(county_id, {})

    try:
        opening   = float(record.get("opening_bid") or 0)
        final     = float(record.get("final_sale_price") or 0)
        assessed  = float(record.get("assessed_value") or 0)
        surplus   = final - opening if final and opening else 0
    except (ValueError, TypeError):
        return None

    # Normalize sale_date to ISO format if we can extract one
    parsed_date = _extract_sale_date(record)
    sale_date_iso = parsed_date.isoformat() if parsed_date else (record.get("sale_date") or "").strip()

    return Lead(
        county_id     = county_id,
        county_name   = info.get("name", county_id),
        state         = info.get("state", ""),
        case_number   = (record.get("case_number") or "").strip(),
        address       = _normalize_address(record.get("address") or ""),
        parcel_id     = (record.get("parcel_id") or "").strip(),
        auction_type  = (record.get("auction_type") or "").strip(),
        opening_bid   = opening,
        final_sale_price = final,
        gross_surplus = surplus,
        assessed_value   = assessed,
        sale_date     = sale_date_iso,
        sale_datetime = _extract_sale_datetime(record),
        sold_to       = (record.get("sold_to") or "").strip(),
        source_url    = (record.get("source_url") or "").strip(),
        is_third_party = bool(record.get("is_third_party", False)),
        auction_status = (record.get("auction_status") or "").strip(),
        scraped_at    = datetime.now().isoformat(timespec="seconds"),
        source_file   = source_file,
    )


def _score_lead(lead: Lead) -> tuple[str, str]:
    s = lead.gross_surplus
    reasons = []

    if s >= 100_000:
        score = "A+"
        reasons.append(f"${s:,.0f} surplus ≥ $100K")
    elif s >= 50_000:
        score = "A"
        reasons.append(f"${s:,.0f} surplus ≥ $50K")
    elif s >= 25_000:
        score = "B"
        reasons.append(f"${s:,.0f} surplus ≥ $25K")
    elif s >= 10_000:
        score = "C"
        reasons.append(f"${s:,.0f} surplus ≥ $10K")
    else:
        score = "—"
        reasons.append("below threshold")

    if lead.is_third_party:
        reasons.append("3rd party bidder ✓")
    if lead.address:
        reasons.append("address known")
    if lead.parcel_id:
        reasons.append("parcel ID known")

    return score, " | ".join(reasons)


# ═══════════════════════════════════════════════════════════════════════
# Verification status model  (HARDENING PASS — Parts 1-6)
#
# Strict separation of confidence layers:
#   • Auction data can create a POSSIBLE lead        → apparent_surplus
#   • PropertyRadar can ENRICH a lead                → estimated_surplus
#   • Only docket / official records CONFIRM surplus → confirmed_surplus
#
# A lead is confirmed_surplus ONLY if every required proof field is present.
# ═══════════════════════════════════════════════════════════════════════

# Classifications a docket scrape can assign.
_POSITIVE_CLASSIFICATIONS = {"green", "yellow"}   # reviewed AND still viable
_NEGATIVE_CLASSIFICATIONS = {"red", "killed"}     # reviewed, NOT viable

# Required proof fields for confirmed_surplus (spec Part 3).
def _has_required_proof(lead) -> bool:
    """
    True only if the lead carries every proof field required to call it
    confirmed_surplus. Any missing field => not confirmed.
    """
    if not lead.county_id:
        return False
    if not lead.case_number:
        return False
    if lead.true_surplus is None or lead.true_surplus <= 0:
        return False
    if not (lead.docket_url or lead.source_url):
        return False
    if not lead.proof_of_surplus:
        return False
    if not lead.sale_date:
        return False
    if not lead.final_sale_price or lead.final_sale_price <= 0:
        return False
    if (lead.classification or "").strip().lower() not in _POSITIVE_CLASSIFICATIONS:
        return False
    return True


def assign_status_fields(lead) -> None:
    """
    Assign research_status, lead_quality, money_status, evidence_level,
    pipeline_ready on a Lead in place.

    This is the single chokepoint that decides whether a lead may be called
    confirmed surplus. It is deliberately conservative: when in doubt, downgrade.
    """
    classification = (lead.classification or "").strip().lower()
    has_docket = bool(classification) or lead.prayer_amount > 0 or bool(lead.docket_url)
    has_pr     = bool(getattr(lead, "enriched", False)) or bool(getattr(lead, "owner_name", ""))

    # ---- lead_quality: mirrors docket classification, else unknown ----
    if classification in _POSITIVE_CLASSIFICATIONS or classification in _NEGATIVE_CLASSIFICATIONS:
        lead.lead_quality = classification
    else:
        lead.lead_quality = "unknown"

    # ---- killed / red: reviewed but NOT viable (spec Part 6) ----
    if classification in _NEGATIVE_CLASSIFICATIONS:
        lead.research_status = "docket_reviewed"
        lead.evidence_level  = "docket_reviewed"
        lead.money_status    = "no_surplus" if classification == "killed" else "unknown"
        lead.pipeline_ready  = False
        return

    # ---- positive classification: candidate for confirmed_surplus ----
    if classification in _POSITIVE_CLASSIFICATIONS:
        if _has_required_proof(lead):
            lead.research_status = "docket_reviewed"
            lead.money_status    = "confirmed_surplus"
            lead.evidence_level  = "docket_confirmed"
            lead.pipeline_ready  = True
        else:
            # Reviewed green/yellow but missing proof => DOWNGRADE (spec Part 3)
            lead.research_status = "docket_reviewed"
            lead.money_status    = "estimated_surplus" if has_pr else "apparent_surplus"
            lead.evidence_level  = "docket_reviewed"
            lead.pipeline_ready  = False
        return

    # ---- no docket classification: PR-enriched or auction-only ----
    if has_pr:
        # PropertyRadar enriched, but PR does NOT verify surplus (spec Part 4)
        lead.research_status = "property_enriched"
        lead.money_status    = "estimated_surplus"
        lead.evidence_level  = "property_enriched"
        lead.pipeline_ready  = False
        return

    # ---- auction-only: apparent surplus, never confirmed (spec Part 5) ----
    lead.research_status = "auction_only"
    lead.money_status    = "apparent_surplus"
    lead.evidence_level  = "auction_only"
    lead.pipeline_ready  = False


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════
def load_all_leads(
    min_surplus: float = 10_000,
    require_third_party: bool = True,
    counties: Optional[list[str]] = None,
    window_days: int = 14,
    verbose: bool = True,
) -> list[Lead]:
    """
    Load all qualifying leads from raw JSONL files across all counties.

    Filters applied (in order):
      1. is_third_party (must be True if require_third_party)
      2. gross_surplus >= min_surplus
      3. sale_date must be parseable
      4. sale_date >= (today - window_days)  ← NEW in v2

    Args:
        min_surplus: Minimum gross surplus required to qualify (default $10K)
        require_third_party: Only include 3rd party bidder wins (default True)
        counties: Optional list of county_ids to include (default: all 10)
        window_days: Maximum age of sale_date in days (default 14)
        verbose: Print summary of what was filtered out

    Returns:
        List of Lead objects, sorted by gross_surplus descending.
    """
    # Load docket scraper results once for the whole run
    _docket_lookup = _load_docket_data()

    target_counties = counties or list(COUNTY_INFO.keys())
    today = date.today()
    cutoff = today - timedelta(days=window_days)
    leads: list[Lead] = []

    # Track what got filtered out, per county
    stats = {
        cid: {"raw": 0, "kept": 0, "not_3rd_party": 0, "below_min": 0,
              "no_date": 0, "out_of_window": 0}
        for cid in target_counties
    }

    for county_id in target_counties:
        jsonl_path = _latest_jsonl_for_county(county_id)
        if not jsonl_path:
            if verbose:
                print(f"⚠ No data file found for {county_id}")
            continue

        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                stats[county_id]["raw"] += 1

                lead = _parse_lead(record, county_id, str(jsonl_path.name))
                if not lead:
                    continue

                # Filter 1: 3rd party
                if require_third_party and not lead.is_third_party:
                    stats[county_id]["not_3rd_party"] += 1
                    continue

                # Filter 2: minimum surplus
                if lead.gross_surplus < min_surplus:
                    stats[county_id]["below_min"] += 1
                    continue

                # Filter 3: sale_date must be parseable
                parsed_date = _extract_sale_date(record)
                if not parsed_date:
                    stats[county_id]["no_date"] += 1
                    continue

                # Filter 4: sale_date within window_days of today
                if parsed_date < cutoff:
                    stats[county_id]["out_of_window"] += 1
                    continue

                # Score and keep
                lead.score, lead.score_reason = _score_lead(lead)
                stats[county_id]["kept"] += 1
                # Merge docket-scraper data if available.
                # FP-3 fix: if no docket data, true_surplus stays None
                # (NOT defaulted to gross_surplus). Apparent-only leads keep
                # true_surplus=None as the explicit "not verified" signal.
                _norm = _normalize_case_for_lookup(lead.case_number)
                _docket = _docket_lookup.get((lead.county_id, _norm))
                if _docket:
                    _apply_docket_to_lead(lead, _docket, lead.county_id)
                # Assign the verification status model (FP-6 gate)
                assign_status_fields(lead)
                leads.append(lead)

    leads.sort(key=lambda x: x.gross_surplus, reverse=True)

    # Print filter audit if verbose
    if verbose:
        total_raw = sum(s["raw"] for s in stats.values())
        total_kept = sum(s["kept"] for s in stats.values())
        total_dropped_window = sum(s["out_of_window"] for s in stats.values())
        total_dropped_date = sum(s["no_date"] for s in stats.values())

        print(f"\n  Date filter: keeping leads sold on or after {cutoff.isoformat()} (last {window_days} days)")
        print(f"  Loaded {total_kept} qualifying leads from {total_raw} raw records.")
        if total_dropped_window or total_dropped_date:
            print(f"  Dropped {total_dropped_window} as out-of-window, {total_dropped_date} with no parseable date.")

        # Show per-county breakdown if anything was dropped for date reasons
        if total_dropped_window or total_dropped_date:
            print("\n  Per-county date-filter impact:")
            for cid in target_counties:
                s = stats[cid]
                if s["out_of_window"] > 0 or s["no_date"] > 0:
                    print(f"    {cid:<18}: kept {s['kept']:>2}, "
                          f"dropped {s['out_of_window']:>2} out-of-window, "
                          f"{s['no_date']:>2} no-date")

    return leads


def get_summary(leads: list[Lead]) -> dict:
    by_county: dict[str, dict] = {}
    by_state: dict[str, dict] = {"FL": {"leads": 0, "surplus": 0.0},
                                  "OH": {"leads": 0, "surplus": 0.0}}
    by_score = {"A+": 0, "A": 0, "B": 0, "C": 0}

    for lead in leads:
        cid = lead.county_id
        if cid not in by_county:
            by_county[cid] = {
                "county_id":   cid,
                "county_name": lead.county_name,
                "state":       lead.state,
                "leads":       0,
                "surplus":     0.0,
                "top_lead":    0.0,
            }
        by_county[cid]["leads"] += 1
        by_county[cid]["surplus"] += lead.gross_surplus
        by_county[cid]["top_lead"] = max(by_county[cid]["top_lead"], lead.gross_surplus)

        if lead.state in by_state:
            by_state[lead.state]["leads"] += 1
            by_state[lead.state]["surplus"] += lead.gross_surplus

        if lead.score in by_score:
            by_score[lead.score] += 1

    return {
        "generated_at":    datetime.now().isoformat(timespec="seconds"),
        "total_leads":     len(leads),
        # FP-4 fix: this total is gross/apparent surplus ONLY. It is NOT
        # confirmed money. dashboard_data.py recomputes confirmed/estimated
        # totals from the verification status model.
        "total_apparent_surplus": sum(l.gross_surplus for l in leads),
        "by_county":       sorted(by_county.values(), key=lambda x: x["surplus"], reverse=True),
        "by_state":        by_state,
        "by_score":        by_score,
        "top_5_leads":     [
            {
                "county":      l.county_name,
                "state":       l.state,
                "case_number": l.case_number,
                "address":     l.address,
                "apparent_surplus": l.gross_surplus,
                "sale_price":  l.final_sale_price,
                "sale_date":   l.sale_date,
                "score":       l.score,
            }
            for l in leads[:5]
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# CLI for quick verification
# ═══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("  SurplusIQ — Data Loader Verification (v2 with 14-day cutoff)")
    print("=" * 70)
    print(f"\n📂 Reading from: {RAW_DIR}\n")

    leads = load_all_leads()
    summary = get_summary(leads)

    print(f"\n✓ Total APPARENT surplus (auction math, not confirmed): "
          f"${summary['total_apparent_surplus']:,.0f}\n")

    print("─" * 70)
    print("  BY STATE")
    print("─" * 70)
    for state, data in summary["by_state"].items():
        print(f"  {state}: {data['leads']:>3} leads | ${data['surplus']:>14,.0f}")

    print("\n" + "─" * 70)
    print("  BY COUNTY (sorted by surplus)")
    print("─" * 70)
    for c in summary["by_county"]:
        print(f"  {c['county_name']:<14} ({c['state']}): {c['leads']:>3} leads | "
              f"${c['surplus']:>14,.0f} | top: ${c['top_lead']:>11,.0f}")

    print("\n" + "─" * 70)
    print("  BY SCORE")
    print("─" * 70)
    for score, count in summary["by_score"].items():
        bar = "█" * count
        print(f"  {score:<3}: {count:>3}  {bar}")

    print("\n" + "─" * 70)
    print("  TOP 5 LEADS")
    print("─" * 70)
    for i, l in enumerate(summary["top_5_leads"], 1):
        print(f"  #{i}  ${l['surplus']:>11,.0f} | {l['score']:<3} | "
              f"{l['county']}, {l['state']} | sold {l['sale_date']} | {l['case_number']}")
        if l['address']:
            print(f"       {l['address'][:60]}")

    print("\n" + "=" * 70)
    print("  ✓ Data loader v2 operational. 14-day cutoff enforced.")
    print("=" * 70)
