"""
SurplusIQ — PropertyRadar Enrichment Module

For each qualifying lead, fetches property + owner intelligence from PropertyRadar:
  • Mortgage balance(s)
  • Available equity
  • Owner name, mailing address, phone, email
  • Lien count and types
  • Ownership length / last sale info

This is the layer that turns "interesting auction sale" into "actionable lead":
without it, the team is calling property owners who might have a $400K mortgage
swallowing the apparent surplus.

PRICING WARNING:
  PropertyRadar charges 1 export credit per successful property record returned.
  Always use --dry-run first to see how many credits a run will cost.
  Use Purchase=0 in the request body to get counts without burning credits.

Usage:
  # Dry run — counts how many properties match, doesn't pull data
  python -m core.enrichment.propertyradar --dry-run

  # Actual enrichment — burns 1 credit per lead
  python -m core.enrichment.propertyradar

  # Just a single county
  python -m core.enrichment.propertyradar --county montgomery-oh

  # Limit to top N leads (smart way to test live without paying for all 124)
  python -m core.enrichment.propertyradar --top 10
"""

from __future__ import annotations
import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import date, datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

# ─── Setup project paths so this can run as a module from anywhere ───
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.loader import load_all_leads, Lead


# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════
PR_API_BASE = "https://api.propertyradar.com/v1"
# Read from environment ONLY. No hardcoded fallback — a missing or empty
# PROPERTYRADAR_TOKEN must fail the run loudly so the run can't silently
# tag leads as "no PR match" when the real cause is unauthenticated.
PR_API_TOKEN = (os.environ.get("PROPERTYRADAR_TOKEN") or "").strip()

# Conservative rate limit — be polite to their API
REQUEST_DELAY_SEC = 0.5

# Fields we want returned per property — using PropertyRadar's REAL field names
PR_FIELDS = [
    "RadarID",
    "Address",
    "City",
    "State",
    "ZipFive",
    "County",
    "APN",
    "PType",
    "Owner",
    "OwnerFirstName",
    "OwnerLastName",
    "OwnerAddress",
    "OwnerCity",
    "OwnerState",
    "OwnerZipFive",
    "OwnerPhone",
    "OwnerEmail",
    "isSameMailingOrExempt",
    "AVM",
    "AssessedValue",
    "AvailableEquity",
    "EquityPercent",
    "TotalLoanBalance",
    "NumberLoans",
    "FirstAmount",
    "FirstDate",
    "FirstPurpose",
    "FirstLenderOriginal",
    "LastTransferRecDate",
    "LastTransferValue",
    "Beds",
    "Baths",
    "SqFt",
    "YearBuilt",
    "Pool",
    "isListedForSale",
    "isMailVacant",
    "isSiteVacant",
    "inForeclosure",
]


# ═══════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class EnrichedLead:
    # Original lead fields
    county_id: str
    county_name: str
    state: str
    case_number: str
    address: str
    parcel_id: str
    final_sale_price: float
    opening_bid: float
    gross_surplus: float
    sale_date: str
    sold_to: str
    score: str

    # PropertyRadar enrichment
    pr_match: bool = False               # Was a PR record found?
    pr_match_count: int = 0              # How many properties matched our address
    pr_radar_id: Optional[str] = None
    pr_owner_name: Optional[str] = None
    pr_mailing_address: Optional[str] = None
    pr_mailing_city: Optional[str] = None
    pr_mailing_state: Optional[str] = None
    pr_mailing_zip: Optional[str] = None
    pr_estimated_value: float = 0.0
    pr_total_loan_balance: float = 0.0
    pr_available_equity: float = 0.0
    pr_first_loan_amount: float = 0.0
    pr_first_loan_type: Optional[str] = None
    pr_second_loan_amount: float = 0.0
    pr_years_owned: Optional[int] = None
    pr_owner_occupied: Optional[bool] = None
    pr_in_tax_delinquency: Optional[bool] = None
    pr_involuntary_lien: Optional[bool] = None
    pr_property_type: Optional[str] = None
    pr_year_built: Optional[int] = None
    pr_sqft: Optional[int] = None
    pr_bedrooms: Optional[int] = None
    pr_bathrooms: Optional[float] = None

    # Derived intelligence
    real_surplus_estimate: float = 0.0   # gross_surplus minus encumbrances
    debt_coverage_ratio: float = 0.0     # final_sale / total_debt
    is_clean_surplus: bool = False       # No 2nd mortgage, no involuntary liens
    enrichment_status: str = "pending"   # pending / matched / no_match / error
    enrichment_notes: str = ""
    enriched_at: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# PropertyRadar API client
# ═══════════════════════════════════════════════════════════════════════════
class PropertyRadarClient:
    """
    Minimal PropertyRadar API client for surplus funds enrichment.
    Uses /v1/properties endpoint with Criteria objects.
    """

    def __init__(self, token: str, dry_run: bool = False):
        self.token = token
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        # Counters for the run summary
        self.calls_made = 0
        self.matches_found = 0
        self.misses = 0
        self.errors = 0
        self.credits_burned = 0  # 1 per matched property when dry_run is False
        self.total_cost_usd = 0.0  # sum of totalCost from each response

    # ─── Request helper with verbose logging ───────────────────────────────
    def _post(self, path: str, *, params: dict, body: dict, label: str) -> requests.Response:
        url = f"{PR_API_BASE}{path}"
        print(f"  → POST {url}")
        print(f"     params: {json.dumps(params, sort_keys=True)}")
        print(f"     body:   {json.dumps(body, sort_keys=True)[:600]}")
        resp = self.session.post(url, params=params, json=body, timeout=30)
        time.sleep(REQUEST_DELAY_SEC)
        head = (resp.text or "")[:600]
        print(f"  ← {resp.status_code} {label}  body[:600]={head!r}")
        return resp

    def _properties_call(self, criteria: list, *, label: str) -> dict:
        """Single POST to /v1/properties. Purchase honored by self.dry_run."""
        params = {
            "Fields": ",".join(PR_FIELDS),
            "Limit": 5,
            "Purchase": 0 if self.dry_run else 1,
            "Start": 0,
        }
        self.calls_made += 1
        try:
            resp = self._post(
                "/properties",
                params=params,
                body={"Criteria": criteria},
                label=label,
            )
        except Exception as e:
            self.errors += 1
            return {"error": str(e)}
        if resp.status_code != 200:
            self.errors += 1
            return {
                "error": f"properties HTTP {resp.status_code}",
                "body": (resp.text or "")[:500],
            }
        try:
            return resp.json()
        except Exception as e:
            self.errors += 1
            return {"error": f"json decode: {e}", "body": (resp.text or "")[:500]}

    def search_by_address(self, street: str, city: str, state: str, zipcode: str = "") -> dict:
        """Look up a property by address against /v1/properties.

        PropertyRadar Criteria field names (verbatim from
        developers.propertyradar.com): SiteAddress, SiteCity, SiteState,
        ZipFive. Each criterion is {"name": "<X>", "value": [...]} per the
        Criteria Reference. There is no raw-address parameter.

        Strategy:
          1. Direct POST /v1/properties with whatever address fields we have.
             This is the fast path for clean addresses.
          2. If that returns 0 matches and we have a street, fall back to
             POST /v1/suggestions/SiteAddress (input field name is
             SiteAddressInput, NOT SuggestionInput), take the top suggestion's
             returned Criteria, and re-query /v1/properties.

        Purchase: 0 when self.dry_run (free; counts/RadarID only), 1 otherwise.
        """
        if not street and not city and not zipcode:
            return {"error": "no address components to search"}

        # ─── STEP 1: direct properties query with what we have ────────────
        # PropertyRadar /v1/properties accepts SiteAddress + ZipFive as
        # address-anchored criteria. SiteCity and SiteState are NOT valid
        # at /properties (the API returns "Unexpected Criterion: SiteCity"
        # / "Unexpected Criterion: SiteState" — verified via probe run
        # 26481516049). City/state are only used for the suggestion
        # fallback below. Address + zip is enough to disambiguate in
        # practice; when zip is missing we fall through to suggestions.
        direct_criteria = []
        if street:
            direct_criteria.append({"name": "SiteAddress", "value": [street]})
        if zipcode:
            direct_criteria.append({"name": "ZipFive", "value": [zipcode]})

        if not direct_criteria:
            return {"error": "no usable criteria after filtering (need street or zip)"}

        data = self._properties_call(direct_criteria, label="direct")
        if "error" in data:
            return data

        results = data.get("results") or data.get("Results") or []
        total = data.get("totalResultCount") or data.get("totalCount") or len(results)
        self.total_cost_usd += float(data.get("totalCost") or 0)
        if results or not street:
            return data

        # ─── STEP 2: fall back to suggestion → normalized criteria ────────
        # PropertyRadar stores addresses with ordinal suffixes ("154TH" not
        # "154"), so unnormalized strings can miss. /v1/suggestions/SiteAddress
        # returns canonical Criteria we can re-query with. The suggestion
        # endpoint accepts SiteState as a scoping criterion (unlike the
        # /properties endpoint, where it's rejected).
        suggest_body = {"SiteAddressInput": street, "Limit": 5}
        if state:
            suggest_body["Criteria"] = [
                {"name": "SiteState", "value": [state.upper()]}
            ]
        self.calls_made += 1
        try:
            sresp = self._post(
                "/suggestions/SiteAddress",
                params={},
                body=suggest_body,
                label="suggest",
            )
        except Exception as e:
            self.errors += 1
            return {"error": f"suggestion: {e}"}

        if sresp.status_code != 200:
            self.errors += 1
            return {
                "error": f"suggestion HTTP {sresp.status_code}",
                "body": (sresp.text or "")[:500],
            }
        try:
            sjson = sresp.json()
        except Exception as e:
            return {"error": f"suggestion json decode: {e}", "body": (sresp.text or "")[:500]}

        sugs = sjson.get("results") or sjson.get("Results") or []
        if not sugs:
            return {
                "results": [], "totalCost": 0, "resultCount": 0,
                "totalResultCount": 0, "_no_suggestion_match": True,
            }
        normalized_criteria = sugs[0].get("Criteria") or []
        normalized_label = sugs[0].get("Label", "")
        if not normalized_criteria:
            self.errors += 1
            return {"error": "suggestion response missing Criteria"}

        data2 = self._properties_call(normalized_criteria, label="post-suggest")
        if "error" not in data2:
            data2["_normalized_label"] = normalized_label
            self.total_cost_usd += float(data2.get("totalCost") or 0)
        return data2

# ═══════════════════════════════════════════════════════════════════════════
# Address normalization
# ═══════════════════════════════════════════════════════════════════════════
def parse_address(raw: str) -> tuple:
    """
    Pull street, city, state, zip out of the messy address strings we have.

    Florida format examples (from RealForeclose):
      "9490 NW 20 PL"               (just street, no city)
      "Property Address: 7328 W 29 LN"
      "1234 MAIN ST  PLANTATION FL 33324"

    Ohio format examples:
      "Property Address: 2210 ARBOR BLVD\nMORAINE , 45439"
      "1630 PIPER LANE, UNIT 208"

    Returns: (street, city, state, zip)
    Best-effort — if we can't parse cleanly, returns just the street.
    """
    import re
    a = (raw or "").replace("Property Address:", "").strip()

    # Try to peel off a trailing zip code
    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b\s*$", a)
    zipcode = zip_match.group(1) if zip_match else ""
    if zip_match:
        a = a[:zip_match.start()].rstrip(", ").strip()

    # Try to peel off a trailing 2-letter state (FL or OH)
    state_match = re.search(r"\b(FL|OH)\b\s*,?\s*$", a, re.IGNORECASE)
    state = state_match.group(1).upper() if state_match else ""
    if state_match:
        a = a[:state_match.start()].rstrip(", ").strip()

    # Try to split into street / city if there's a clear separator like "\n" or ", "
    # Ohio typically has "STREET\nCITY"
    # Florida often has just street alone
    parts = re.split(r"\n|,", a, maxsplit=1)
    if len(parts) == 2:
        street = parts[0].strip()
        city = parts[1].strip().rstrip(",")
    else:
        street = a.strip()
        city = ""

    return street, city, state, zipcode


# ═══════════════════════════════════════════════════════════════════════════
# Enrichment logic
# ═══════════════════════════════════════════════════════════════════════════
def state_from_county_id(county_id: str) -> str:
    return county_id.rsplit("-", 1)[-1].upper()


def derive_intelligence(enriched: EnrichedLead) -> EnrichedLead:
    """
    Compute the derived fields the dashboard cares about most:
    - real_surplus_estimate: gross_surplus minus encumbrances
    - debt_coverage_ratio: how much of the sale price was covered by the debt
    - is_clean_surplus: no 2nd mortgage, no involuntary liens
    """
    # Real surplus = gross surplus minus what the lender(s) get paid back from the sale.
    # In a foreclosure auction, the FIRST lender's debt gets satisfied from the sale proceeds first.
    # Anything ABOVE that goes to junior lienholders, then to the homeowner.
    # If first loan balance > sale price, the foreclosing party absorbs the loss — no surplus to claim.
    # If first loan balance < sale price, the difference is the actual surplus pool.

    if enriched.pr_match and enriched.pr_total_loan_balance > 0:
        # Refined estimate: sale price minus actual encumbrances
        real_surplus = enriched.final_sale_price - enriched.pr_total_loan_balance
        enriched.real_surplus_estimate = max(0, real_surplus)

        if enriched.final_sale_price > 0:
            enriched.debt_coverage_ratio = enriched.pr_total_loan_balance / enriched.final_sale_price
    else:
        # No PR match — fall back to the gross surplus from auction data
        enriched.real_surplus_estimate = enriched.gross_surplus

    # Clean = no 2nd mortgage AND no involuntary lien
    enriched.is_clean_surplus = (
        enriched.pr_match
        and enriched.pr_second_loan_amount == 0
        and not enriched.pr_involuntary_lien
        and not enriched.pr_in_tax_delinquency
    )

    return enriched


def map_pr_record_to_enriched(lead: Lead, pr_record: dict, match_count: int) -> EnrichedLead:
    """Take a PropertyRadar record dict and merge it onto a Lead."""
    e = EnrichedLead(
        county_id=lead.county_id,
        county_name=lead.county_name,
        state=lead.state,
        case_number=lead.case_number,
        address=lead.address,
        parcel_id=lead.parcel_id,
        final_sale_price=lead.final_sale_price,
        opening_bid=lead.opening_bid,
        gross_surplus=lead.gross_surplus,
        sale_date=lead.sale_date,
        sold_to=lead.sold_to,
        score=lead.score,
    )

    if not pr_record:
        e.enrichment_status = "no_match"
        e.enrichment_notes = "No properties returned for this address"
        e.enriched_at = datetime.now().isoformat()
        return derive_intelligence(e)

    e.pr_match = True
    e.pr_match_count = match_count
    e.pr_radar_id = pr_record.get("RadarID")
    e.pr_owner_name = pr_record.get("Owner") or (
        f"{pr_record.get('OwnerFirstName', '')} {pr_record.get('OwnerLastName', '')}".strip()
    )
    e.pr_mailing_address = pr_record.get("OwnerAddress")
    e.pr_mailing_city = pr_record.get("OwnerCity")
    e.pr_mailing_state = pr_record.get("OwnerState")
    e.pr_mailing_zip = pr_record.get("OwnerZipFive")
    e.pr_estimated_value = float(pr_record.get("AVM") or 0)
    e.pr_total_loan_balance = float(pr_record.get("TotalLoanBalance") or 0)
    e.pr_available_equity = float(pr_record.get("AvailableEquity") or 0)
    e.pr_first_loan_amount = float(pr_record.get("FirstAmount") or 0)
    e.pr_first_loan_type = pr_record.get("FirstPurpose")
    # PR doesn't expose 2nd loan amount directly — derive: total - first if multiple loans exist
    num_loans = pr_record.get("NumberLoans") or 0
    if num_loans > 1 and e.pr_total_loan_balance > 0 and e.pr_first_loan_amount > 0:
        e.pr_second_loan_amount = max(0, e.pr_total_loan_balance - e.pr_first_loan_amount)
    else:
        e.pr_second_loan_amount = 0
    # YearsOwned isn't a direct field — calculate from LastTransferRecDate
    last_xfer = pr_record.get("LastTransferRecDate")
    if last_xfer:
        try:
            from datetime import datetime as _dt
            xfer_year = _dt.fromisoformat(str(last_xfer).replace("Z", "+00:00")).year
            e.pr_years_owned = datetime.now().year - xfer_year
        except Exception:
            e.pr_years_owned = None
    e.pr_owner_occupied = pr_record.get("isSameMailingOrExempt")
    # PR doesn't expose tax delinquency or involuntary lien directly via API fields
    e.pr_in_tax_delinquency = None
    # Use NumberLoans > 1 as proxy for "additional encumbrances exist"
    e.pr_involuntary_lien = (num_loans or 0) > 1
    e.pr_property_type = pr_record.get("PType")
    e.pr_year_built = pr_record.get("YearBuilt")
    e.pr_sqft = pr_record.get("SqFt")
    e.pr_bedrooms = pr_record.get("Beds")
    bath = pr_record.get("Baths")
    e.pr_bathrooms = float(bath) if bath is not None else None

    e.enrichment_status = "matched"
    e.enriched_at = datetime.now().isoformat()
    return derive_intelligence(e)


def enrich_leads(
    leads: list,
    client: PropertyRadarClient,
    output_dir: Path,
    progress_every: int = 5,
) -> list:
    """
    Run PropertyRadar enrichment over every lead. Returns list of EnrichedLead.
    Writes a JSONL file per county.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    enriched_results = []
    by_county = {}

    for i, lead in enumerate(leads, 1):
        street, city, state, zipcode = parse_address(lead.address)

        if not state:
            state = state_from_county_id(lead.county_id)

        if not street:
            # Can't enrich without at least a street address
            enriched_results.append(map_pr_record_to_enriched(lead, {}, 0))
            continue

        # Hit the API
        result = client.search_by_address(street, city, state, zipcode)

        if "error" in result:
            e = map_pr_record_to_enriched(lead, {}, 0)
            e.enrichment_status = "error"
            e.enrichment_notes = result.get("error", "")[:200]
            e.enriched_at = datetime.now().isoformat()
            enriched_results.append(e)
        else:
            results_list = result.get("results", []) or result.get("Results", []) or []
            count = result.get("totalResultCount") or result.get("totalCount") or len(results_list)

            # Track API spend
            client.total_cost_usd += float(result.get("totalCost") or 0)

            if results_list:
                client.matches_found += 1
                if not client.dry_run:
                    client.credits_burned += 1
                e = map_pr_record_to_enriched(lead, results_list[0], count)
            else:
                client.misses += 1
                e = map_pr_record_to_enriched(lead, {}, count)

            enriched_results.append(e)

        by_county.setdefault(lead.county_id, []).append(asdict(enriched_results[-1]))

        # Progress indicator
        if i % progress_every == 0 or i == len(leads):
            mode = "DRY-RUN" if client.dry_run else "LIVE"
            print(
                f"  [{mode}] {i:>3}/{len(leads)} | "
                f"matches: {client.matches_found:>3} | "
                f"misses: {client.misses:>3} | "
                f"errors: {client.errors:>3} | "
                f"credits: {client.credits_burned}"
            )

    # Write per-county JSONL files
    for county_id, records in by_county.items():
        out = output_dir / f"{county_id}_{today}_enriched.jsonl"
        with open(out, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    return enriched_results


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
def print_summary(enriched: list, client: PropertyRadarClient):
    """Print a clean run summary."""
    n = len(enriched)
    matched = sum(1 for e in enriched if e.enrichment_status == "matched")
    no_match = sum(1 for e in enriched if e.enrichment_status == "no_match")
    error = sum(1 for e in enriched if e.enrichment_status == "error")

    total_pr_value = sum(e.pr_estimated_value for e in enriched if e.pr_match)
    total_pr_debt = sum(e.pr_total_loan_balance for e in enriched if e.pr_match)
    total_real_surplus = sum(e.real_surplus_estimate for e in enriched)
    clean_count = sum(1 for e in enriched if e.is_clean_surplus)

    print()
    print("=" * 72)
    print(f"  ENRICHMENT SUMMARY  ({'DRY-RUN' if client.dry_run else 'LIVE'})")
    print("=" * 72)
    print(f"  Leads processed:        {n}")
    print(f"  PropertyRadar matches:  {matched}  ({matched/n*100:.0f}% match rate)")
    print(f"  No-match:               {no_match}")
    print(f"  Errors:                 {error}")
    print(f"  API calls made:         {client.calls_made}")
    print(f"  Credits burned:         {client.credits_burned}")
    print(f"  Total API cost:         ${client.total_cost_usd:.2f}")
    print()
    print(f"  Aggregate property value (PR estimate): ${total_pr_value:>14,.0f}")
    print(f"  Aggregate loan balances:                ${total_pr_debt:>14,.0f}")
    print(f"  Real surplus estimate (refined):        ${total_real_surplus:>14,.0f}")
    print(f"  'Clean surplus' leads (no 2nd / liens): {clean_count}")
    print()

    # Show top 5 enriched leads by real surplus estimate
    matched_leads = [e for e in enriched if e.pr_match]
    if matched_leads:
        top = sorted(matched_leads, key=lambda e: e.real_surplus_estimate, reverse=True)[:5]
        print("  TOP 5 BY REFINED REAL SURPLUS")
        print("  " + "-" * 68)
        for i, e in enumerate(top, 1):
            print(f"  #{i} ${e.real_surplus_estimate:>12,.0f} | {e.score:<3} | {e.county_name}, {e.state}")
            print(f"      {e.address[:60]}")
            print(f"      Owner: {e.pr_owner_name}  | Equity: ${e.pr_available_equity:,.0f}")
            print(f"      Sale: ${e.final_sale_price:,.0f}  | Loan balance: ${e.pr_total_loan_balance:,.0f}")
            print()
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="PropertyRadar enrichment for SurplusIQ leads")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't burn credits — just verify which leads have matches")
    parser.add_argument("--top", type=int, default=None,
                        help="Only enrich top N leads by gross surplus (e.g. --top 10)")
    parser.add_argument("--county", type=str, default=None,
                        help="Only enrich a specific county (e.g. --county montgomery-oh)")
    parser.add_argument("--score", type=str, default=None,
                        choices=["A+", "A", "B", "C"],
                        help="Only enrich leads of this score tier")
    parser.add_argument("--probe-address", type=str, default=None, metavar="STREET|CITY|STATE|ZIP",
                        help="One-shot Purchase=0 smoke test against a single address. "
                             "Pipe-separated, e.g. '1253 MCINTOSH AVE|AKRON|OH|44314'. "
                             "Bypasses lead loading. Forces --dry-run.")
    parser.add_argument("--probe-criteria", type=str, default=None, metavar="JSON",
                        help="One-shot Purchase=0 smoke test with raw Criteria JSON. "
                             "Pass a JSON ARRAY, e.g. '[{\"name\":\"APN\",\"value\":[\"6822178\"]}]'. "
                             "Useful for feasibility-probing whether a criterion "
                             "is accepted by /properties on the current PR plan. "
                             "Forces --dry-run.")
    parser.add_argument("--probe-radarid", type=str, default=None, metavar="RADARID",
                        help="GET /v1/properties/{RadarID} smoke test. Free — this "
                             "endpoint does not deduct exports. Use the docs' "
                             "sample RadarID 'P8A0E18D' (Groundhog Day House) to "
                             "confirm the endpoint works on the current plan.")
    args = parser.parse_args()

    if not PR_API_TOKEN:
        print(
            "❌ PROPERTYRADAR_TOKEN env var is missing or empty. "
            "Set it as a GitHub Actions secret (or export locally) before "
            "running PR enrichment. No hardcoded fallback is permitted."
        )
        sys.exit(1)
    print(
        f"🔑 PR token loaded: prefix={PR_API_TOKEN[:8]}… suffix=…{PR_API_TOKEN[-4:]} "
        f"length={len(PR_API_TOKEN)}"
    )

    print()
    print("┌" + "─" * 70 + "┐")
    print("│  SurplusIQ — PropertyRadar Enrichment".ljust(71) + "│")
    print("└" + "─" * 70 + "┘")
    print()

    # ─── Probe mode: GET /v1/properties/{RadarID} ─────────────────────────
    if args.probe_radarid:
        radarid = args.probe_radarid.strip()
        print(f"🧪 PROBE-RADARID radarid={radarid!r}")
        client = PropertyRadarClient(token=PR_API_TOKEN, dry_run=True)
        url = f"{PR_API_BASE}/properties/{radarid}"
        # Card carries the lien/loan fields Eric needs (TotalLoanBalance,
        # AvailableEquity, PropertyHasOpenLiens, PropertyHasOpenPersonLiens,
        # AVM, AssessedValue, Owner). Stay under PR's 50-field per-request cap.
        # GET /properties/{RadarID} also requires Purchase — 0 = free read.
        params = {"Fields": "Card", "Purchase": 0}
        print(f"  → GET {url}")
        print(f"     params: {json.dumps(params, sort_keys=True)}")
        try:
            resp = client.session.get(url, params=params, timeout=30)
            head = (resp.text or "")[:1200]
            print(f"  ← {resp.status_code}  body[:1200]={head!r}")
        except Exception as e:
            print(f"  ← EXCEPTION {type(e).__name__}: {e}")
        return

    # ─── Probe mode: raw Criteria JSON, Purchase=0 ────────────────────────
    if args.probe_criteria:
        try:
            criteria = json.loads(args.probe_criteria)
        except Exception as e:
            print(f"❌ --probe-criteria must be valid JSON: {e}")
            sys.exit(2)
        if not isinstance(criteria, list):
            print(f"❌ --probe-criteria must be a JSON array of criterion objects")
            sys.exit(2)
        print(f"🧪 PROBE-CRITERIA criteria={json.dumps(criteria)}")
        print(f"                  Purchase=0 forced (free). No credits will be deducted.")
        client = PropertyRadarClient(token=PR_API_TOKEN, dry_run=True)
        result = client._properties_call(criteria, label="probe-criteria")
        print()
        print("─── PROBE RESULT ───")
        if isinstance(result, dict):
            for k in ("results", "Results"):
                if k in result and isinstance(result[k], list):
                    result[f"{k}_count_for_log"] = len(result[k])
                    result[k] = result[k][:1]
        print(json.dumps(result, indent=2, default=str)[:3000])
        return

    # ─── Probe mode: one address, Purchase=0, full request/response log ───
    if args.probe_address:
        parts = args.probe_address.split("|")
        if len(parts) != 4:
            print("❌ --probe-address must be 'STREET|CITY|STATE|ZIP' (4 pipe-separated parts)")
            sys.exit(2)
        street, city, state, zipcode = [p.strip() for p in parts]
        print(f"🧪 PROBE  street={street!r} city={city!r} state={state!r} zip={zipcode!r}")
        print(f"          Purchase=0 forced (free). No credits will be deducted.")
        client = PropertyRadarClient(token=PR_API_TOKEN, dry_run=True)
        result = client.search_by_address(street, city, state, zipcode)
        print()
        print("─── PROBE RESULT ───")
        # Trim heavy fields for readable log
        if isinstance(result, dict):
            for k in ("results", "Results"):
                if k in result and isinstance(result[k], list):
                    result[f"{k}_count_for_log"] = len(result[k])
                    result[k] = result[k][:1]  # one sample
        print(json.dumps(result, indent=2, default=str)[:3000])
        return

    # Load all qualifying leads
    print("📊 Loading leads...")
    leads = load_all_leads()
    print(f"   ✓ {len(leads)} qualifying leads loaded")

    # Apply filters
    if args.county:
        leads = [l for l in leads if l.county_id == args.county]
        print(f"   → Filtered to {args.county}: {len(leads)} leads")

    if args.score:
        leads = [l for l in leads if l.score == args.score]
        print(f"   → Filtered to score {args.score}: {len(leads)} leads")

    if args.top:
        leads = sorted(leads, key=lambda l: l.gross_surplus, reverse=True)[:args.top]
        print(f"   → Top {args.top} by surplus: {len(leads)} leads")

    if not leads:
        print("   ⚠ No leads to process. Exiting.")
        return

    # Show what we're about to do
    print()
    print(f"⚙️  Configuration:")
    print(f"   Mode:              {'DRY RUN (no credits charged)' if args.dry_run else 'LIVE (will charge credits)'}")
    print(f"   Leads to process:  {len(leads)}")
    print(f"   Estimated cost:    {0 if args.dry_run else len(leads)} credits (worst case)")
    print(f"   API base:          {PR_API_BASE}")
    print(f"   Token:             {PR_API_TOKEN[:8]}...{PR_API_TOKEN[-4:]}")
    print()

    if not args.dry_run:
        print("⚠️  This is a LIVE run — each match will cost 1 PropertyRadar export credit.")
        confirm = input("   Continue? (y/N): ").strip().lower()
        if confirm != "y":
            print("   Aborted.")
            return

    # Run it
    client = PropertyRadarClient(token=PR_API_TOKEN, dry_run=args.dry_run)

    output_dir = PROJECT_ROOT / "data" / "enriched"

    print()
    print("🔍 Enriching leads...")
    enriched = enrich_leads(leads, client, output_dir)

    # Print run summary
    print_summary(enriched, client)

    # Write the master enriched file (everything in one place)
    master_file = output_dir / f"all_enriched_{date.today().isoformat()}.json"
    with open(master_file, "w") as f:
        json.dump([asdict(e) for e in enriched], f, indent=2, default=str)
    print(f"  💾 Master enriched file: {master_file.relative_to(PROJECT_ROOT)}")
    print()


if __name__ == "__main__":
    main()
