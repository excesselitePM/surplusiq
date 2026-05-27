"""
SurplusIQ — Dashboard Data Exporter (HARDENING PASS — false-positive resistant)

Generates two JSON files the dashboard HTML reads:
  docs/data/leads.json    — all qualifying leads + verification status model
  docs/data/summary.json  — separated confirmed/estimated/apparent totals

Core rule (strict separation of confidence layers):
  • Auction math alone        → apparent_surplus  (never confirmed)
  • PropertyRadar enrichment  → estimated_surplus (PR cannot confirm surplus)
  • Docket + proof fields     → confirmed_surplus (only path to confirmed)

The headline never presents apparent surplus as confirmed money.
killed/red leads are kept in leads.json (QA trail) but excluded from the
confirmed total, pipeline-ready count, and top-confirmed list.

Usage:
    python -m core.dashboard_data
"""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

from core.loader import (
    load_all_leads, get_summary, PROJECT_ROOT,
    _POSITIVE_CLASSIFICATIONS, _NEGATIVE_CLASSIFICATIONS,
)


# Classifications that count as "docket-verified" — explicit allowlist.
# "unknown" is excluded because it means the scrape ran but produced no usable data.
DOCKET_VERIFIED_CLASSIFICATIONS = {"green", "yellow", "red", "killed"}


def _load_pr_enrichment() -> dict:
    """Load TODAY's PropertyRadar enrichment file only.

    Hard rule (FP-7 anti-stale): a lead may only be tagged estimated_surplus
    when this-run PR data backs it. We refuse to merge enrichment older than
    today — that prevents a stale tier badge from surviving a failed or
    skipped PR step. If no file dated today exists, return an empty lookup
    so every lead drops to apparent_surplus (or its docket-derived tier).
    """
    enriched_dir = PROJECT_ROOT / "data" / "enriched"
    if not enriched_dir.exists():
        return {}

    from datetime import date as _date
    today_str = _date.today().isoformat()
    todays_file = enriched_dir / f"all_enriched_{today_str}.json"
    if not todays_file.exists():
        print(f"   📡 PropertyRadar enrichment: no file for today ({today_str}) — "
              f"all leads will use docket-derived or apparent_surplus tier")
        return {}

    print(f"   📡 PropertyRadar enrichment: loading {todays_file.name} (today only)")
    try:
        with open(todays_file) as f:
            records = json.load(f)
    except Exception as e:
        print(f"   ⚠ Failed to load PR enrichment: {e}")
        return {}

    lookup = {}
    matched = 0
    for r in records:
        # Only keep records the PR client actually matched this run.
        # Records with pr_match=False are no-match leads that should drop
        # back to apparent_surplus.
        if not r.get("pr_match"):
            continue
        key = (r.get("county_id", ""), r.get("case_number", ""))
        lookup[key] = r
        matched += 1

    print(f"   ✓ {len(records)} PR enrichment records loaded, {matched} matched this run")
    return lookup


def _apply_pr_to_payload(payload_lead: dict, pr_record: dict) -> dict:
    """Merge PropertyRadar enrichment fields onto a payload lead dict."""
    if not pr_record or not pr_record.get("pr_match"):
        return payload_lead

    pr_fields = [
        "pr_match", "pr_radar_id", "pr_owner_name",
        "pr_mailing_address", "pr_mailing_city", "pr_mailing_state", "pr_mailing_zip",
        "pr_estimated_value", "pr_total_loan_balance", "pr_available_equity",
        "pr_first_loan_amount", "pr_first_loan_type", "pr_second_loan_amount",
        "pr_years_owned", "pr_owner_occupied", "pr_in_tax_delinquency",
        "pr_involuntary_lien", "pr_property_type", "pr_year_built",
        "pr_sqft", "pr_bedrooms", "pr_bathrooms",
        "real_surplus_estimate", "debt_coverage_ratio", "is_clean_surplus",
        "enrichment_status",
    ]
    for field in pr_fields:
        if field in pr_record:
            payload_lead[field] = pr_record[field]

    return payload_lead


def _surplus_for_payload(payload_lead: dict) -> tuple:
    """
    Return (amount, bucket) where bucket is one of:
      confirmed_surplus | estimated_surplus | apparent_surplus | no_surplus

    HARDENING (FP-6): the bucket is driven by money_status assigned by the
    loader's verification status model — NOT by classification alone, and
    NOT by trusting true_surplus blindly. The status model has already run
    the proof-field gate, so this function only reads its verdict.
    """
    money_status = (payload_lead.get("money_status") or "unknown").strip().lower()

    if money_status == "confirmed_surplus":
        # Proof gate already passed in the loader => true_surplus is real.
        ts = payload_lead.get("true_surplus")
        return (float(ts) if ts is not None else 0.0, "confirmed_surplus")

    if money_status == "estimated_surplus":
        # PR-refined surplus only when TLB > $0 — the same FP-8 rule that
        # _reassign_status_after_pr enforces. Defensive double-check here
        # so docket-reviewed-but-unproven leads (which the loader also tags
        # estimated_surplus when has_pr is True) can't slip through with
        # an unrefined number.
        tlb = float(payload_lead.get("pr_total_loan_balance") or 0)
        pr = payload_lead.get("real_surplus_estimate")
        if tlb > 0 and pr is not None:
            return (float(pr), "estimated_surplus")
        # No real refinement — surplus number is auction math.
        return (float(payload_lead.get("gross_surplus", 0.0)), "apparent_surplus")

    if money_status == "no_surplus":
        return (0.0, "no_surplus")

    # apparent_surplus or unknown => auction math only
    return (float(payload_lead.get("gross_surplus", 0.0)), "apparent_surplus")


def _reassign_status_after_pr(payload: dict) -> None:
    """
    Re-run the verification status model on a payload dict AFTER PR enrichment
    has been merged. Three-tier model honesty (FP-8):

      estimated_surplus  REQUIRES pr_total_loan_balance > 0 — i.e. PR actually
                         refined the surplus arithmetic. A PR match with
                         TotalLoanBalance == $0 means PR couldn't refine
                         the math (data lag — freshly-foreclosed property
                         hasn't propagated through PR's source data yet),
                         so the lead drops to apparent_surplus. The PR
                         data (owner, lien flags, tax_delinquency, distress)
                         stays attached as INTEL FIELDS on the lead.

      This NEVER upgrades a lead to confirmed_surplus — PR cannot confirm
      surplus (spec Part 4). Leads already docket-classified are left
      untouched.
    """
    classification = (payload.get("classification") or "").strip().lower()

    # Docket-classified leads (positive OR negative) keep the loader's verdict.
    if classification in _POSITIVE_CLASSIFICATIONS or classification in _NEGATIVE_CLASSIFICATIONS:
        return

    if not payload.get("pr_match"):
        return

    tlb = float(payload.get("pr_total_loan_balance") or 0)
    payload["research_status"] = "property_enriched"
    payload["evidence_level"]  = "property_enriched"
    payload["lead_quality"]    = "unknown"
    payload["pipeline_ready"]  = False
    if tlb > 0:
        # Real arithmetic refinement — PR's TLB lets us subtract debt.
        payload["money_status"] = "estimated_surplus"
    else:
        # PR match but no debt data; auction math is what we have. PR
        # intel (owner / lien flags / tax delinquency) stays in the payload.
        payload["money_status"] = "apparent_surplus"


def export_dashboard_data():
    docs_data = PROJECT_ROOT / "docs" / "data"
    docs_data.mkdir(parents=True, exist_ok=True)

    print("📊 Loading leads...")
    leads = load_all_leads()
    summary = get_summary(leads)
    print(f"   ✓ {len(leads)} leads loaded")
    print(f"   ✓ ${summary['total_apparent_surplus']:,.0f} apparent surplus (pre-enrichment)")

    pr_lookup = _load_pr_enrichment()

    leads_payload = []
    pr_matches = 0
    docket_matches = 0
    total_real_surplus = 0.0

    # CountyConfig lookup for manual-verify clerk links per lead.
    try:
        from config.counties import COUNTY_BY_ID
    except Exception:
        COUNTY_BY_ID = {}
    # Counties where the docket is not automatable (Cloudflare). Dashboard
    # surfaces these prominently with a manual-verify link.
    CF_BLOCKED = {"franklin-oh", "hamilton-oh"}

    for l in leads:
        cc = COUNTY_BY_ID.get(l.county_id)
        clerk_search_url = getattr(cc, "clerk_search_url", "") if cc else ""
        payload = {
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
            "sale_datetime":    getattr(l, "sale_datetime", ""),
            "sold_to":          l.sold_to,
            "auction_status":   l.auction_status,
            "score":            l.score,
            "source_url":       getattr(l, "source_url", ""),
            # Manual-verify clerk portal link — always present so Eric can
            # eyeball any case directly. For Franklin/Hamilton (Cloudflare-
            # blocked, PR-fallback only) this is the ONLY path to the docket.
            "clerk_manual_search_url": clerk_search_url,
            "requires_manual_docket_verify": l.county_id in CF_BLOCKED,

            # Docket-enrichment fields
            "classification":         getattr(l, "classification", ""),
            "classification_reason":  getattr(l, "classification_reason", ""),
            "prayer_amount":          getattr(l, "prayer_amount", 0.0),
            "true_surplus":           getattr(l, "true_surplus", None),
            "kill_signals":           getattr(l, "kill_signals", []),
            "proof_of_surplus":       getattr(l, "proof_of_surplus", ""),
            "competing_filers":       getattr(l, "competing_filers", []),
            "additional_parties":     getattr(l, "additional_parties", []),
            "docket_url":             getattr(l, "docket_url", ""),

            # Verification status model (HARDENING — Parts 1-6)
            "research_status":  getattr(l, "research_status", "unknown"),
            "lead_quality":     getattr(l, "lead_quality", "unknown"),
            "money_status":     getattr(l, "money_status", "unknown"),
            "evidence_level":   getattr(l, "evidence_level", "unknown"),
            "pipeline_ready":   getattr(l, "pipeline_ready", False),

            "pr_match": False,
        }

        pr_record = pr_lookup.get((l.county_id, l.case_number))
        if pr_record:
            _apply_pr_to_payload(payload, pr_record)
            if payload.get("pr_match"):
                pr_matches += 1
                # PR merged AFTER loader status assignment. Re-run the status
                # model on the merged payload so a PR-matched auction-only lead
                # is correctly re-tagged property_enriched / estimated_surplus.
                _reassign_status_after_pr(payload)

        # docket match count: any real docket classification
        if (payload.get("classification") or "").strip().lower() in DOCKET_VERIFIED_CLASSIFICATIONS:
            docket_matches += 1

        amount, bucket = _surplus_for_payload(payload)
        payload["best_real_surplus"]   = amount
        payload["real_surplus_source"] = bucket
        # Only confirmed surplus contributes to the confirmed total.
        if bucket == "confirmed_surplus":
            total_real_surplus += amount

        # ── FP-9: priority_rank — surface docket-verified positive-surplus
        # leads at the top of the dashboard so the 7 Summit YELLOW/RED
        # leads don't get buried under 40 auction-math rows.
        #
        # Rank semantics (lower = higher priority on the dashboard):
        #   0  confirmed_surplus (docket + proof field)
        #   1  docket-verified positive: real prayer >= $10K, positive
        #      true_surplus, classification in {green,yellow,red}
        #   2  estimated_surplus (PR-refined math, TLB > $0)
        #   3  apparent_surplus with PR intel (owner/liens attached)
        #   4  apparent_surplus, no PR data
        #   5  killed / no_surplus (still shown but de-emphasized)
        cls_lc = (payload.get("classification") or "").lower()
        ts = payload.get("true_surplus")
        prayer = float(payload.get("prayer_amount") or 0)
        DOCKET_VERIFIED_MIN_PRAYER = 10000.0
        if bucket == "confirmed_surplus":
            payload["priority_rank"] = 0
        elif (cls_lc in ("green", "yellow", "red")
              and prayer >= DOCKET_VERIFIED_MIN_PRAYER
              and ts is not None and ts > 0):
            payload["priority_rank"] = 1
            payload["docket_verified_positive"] = True
        elif bucket == "estimated_surplus":
            payload["priority_rank"] = 2
        elif bucket == "apparent_surplus" and payload.get("pr_match"):
            payload["priority_rank"] = 3
        elif bucket == "apparent_surplus":
            payload["priority_rank"] = 4
        else:
            payload["priority_rank"] = 5

        leads_payload.append(payload)

    # Sort by priority_rank (asc), then by best_real_surplus (desc) within rank.
    # The dashboard frontend can re-sort but the wire order is the audit order.
    leads_payload.sort(key=lambda p: (p.get("priority_rank", 5), -float(p.get("best_real_surplus", 0) or 0)))

    leads_file = docs_data / "leads.json"
    with open(leads_file, "w") as f:
        json.dump(leads_payload, f, indent=2)
    print(f"   ✓ Wrote {leads_file.relative_to(PROJECT_ROOT)}")
    print(f"   ✓ Enrichment coverage: {pr_matches} PR / {docket_matches} docket / {len(leads_payload)} total")
    print(f"   ✓ Total real surplus (best available): ${total_real_surplus:,.0f}")

    # ── HARDENING (FP-4): rebuild ALL summary numbers from money_status ──
    # The headline must NOT present apparent surplus as confirmed money.
    def _bucket(p):
        return (p.get("money_status") or "unknown").strip().lower()

    confirmed = [p for p in leads_payload if _bucket(p) == "confirmed_surplus"]
    estimated = [p for p in leads_payload if _bucket(p) == "estimated_surplus"]
    apparent  = [p for p in leads_payload if _bucket(p) == "apparent_surplus"]
    killed    = [p for p in leads_payload if (p.get("lead_quality") or "") == "killed"]
    red       = [p for p in leads_payload if (p.get("lead_quality") or "") == "red"]
    docket_verified_positive = [p for p in leads_payload if p.get("docket_verified_positive")]

    confirmed_total = sum(p.get("best_real_surplus", 0) for p in confirmed)
    estimated_total = sum(p.get("best_real_surplus", 0) for p in estimated)
    apparent_total  = sum(p.get("best_real_surplus", 0) for p in apparent)
    docket_verified_positive_total = sum(p.get("best_real_surplus", 0) for p in docket_verified_positive)

    def _top5(bucket_list):
        s = sorted(bucket_list, key=lambda p: p.get("best_real_surplus", 0), reverse=True)
        return [{
            "county":      p.get("county_name", ""),
            "state":       p.get("state", ""),
            "case_number": p.get("case_number", ""),
            "address":     p.get("address", ""),
            "surplus":     p.get("best_real_surplus", 0),
            "money_status": p.get("money_status", "unknown"),
            "evidence_level": p.get("evidence_level", "unknown"),
        } for p in s[:5]]

    print(f"   ✓ Confirmed: {len(confirmed)} leads / ${confirmed_total:,.0f}")
    print(f"   ✓ Estimated: {len(estimated)} leads / ${estimated_total:,.0f}")
    print(f"   ✓ Apparent:  {len(apparent)} leads / ${apparent_total:,.0f}")
    print(f"   ✓ 🎯 Docket-verified positive: {len(docket_verified_positive)} leads / ${docket_verified_positive_total:,.0f}")
    print(f"   ✓ Killed: {len(killed)} | Red: {len(red)} (excluded from confirmed)")

    summary_file = docs_data / "summary.json"
    summary_payload = {
        "generated_at":           summary["generated_at"],
        "total_leads":            summary["total_leads"],

        # FP-4: separated, honestly-labeled totals. No single "total_surplus"
        # that conflates confirmed money with auction guesses.
        "confirmed_surplus_total":  confirmed_total,
        "estimated_surplus_total":  estimated_total,
        "apparent_surplus_total":   apparent_total,
        # FP-9 headline: how many leads have a docket-extracted prayer AND
        # positive true_surplus AND a non-killed classification. These are
        # the highest-quality leads on the dashboard.
        "docket_verified_positive_count": len(docket_verified_positive),
        "docket_verified_positive_total": docket_verified_positive_total,

        "confirmed_surplus_count":  len(confirmed),
        "estimated_surplus_count":  len(estimated),
        "apparent_surplus_count":   len(apparent),
        "killed_count":             len(killed),
        "red_count":                len(red),
        "pipeline_ready_count":     sum(1 for p in leads_payload if p.get("pipeline_ready")),

        "pr_matched_count":       pr_matches,
        "docket_matched_count":   docket_matches,

        "by_state":  summary["by_state"],
        "by_county": summary["by_county"],
        "by_score":  summary["by_score"],

        "top_5_confirmed_leads": _top5(confirmed),
        "top_5_estimated_leads": _top5(estimated),
        "top_5_apparent_leads":  _top5(apparent),

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
    print(f"   ✓ CONFIRMED surplus = ${confirmed_total:,.0f} "
          f"({len(confirmed)} leads) — apparent ${apparent_total:,.0f} NOT counted as confirmed")

    return leads_file, summary_file


if __name__ == "__main__":
    leads_file, summary_file = export_dashboard_data()
    print()
    print("=" * 70)
    print("  ✓ Dashboard data export complete")
    print(f"  📁 {leads_file}")
    print(f"  📁 {summary_file}")
    print("=" * 70)
