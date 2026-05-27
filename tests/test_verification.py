"""
SurplusIQ — Verification Hardening Test Suite

Proves the false-positive-resistant status model behaves correctly.
Runs standalone (no pytest required):

    python -m tests.test_verification

Each test maps to a case in the hardening spec, Part 10.
The central guarantee under test: a lead is money_status == "confirmed_surplus"
ONLY when it is docket-classified green/yellow AND carries every proof field.
Everything else is estimated, apparent, no_surplus, or unknown.
"""

from __future__ import annotations
import sys

from core.loader import Lead, assign_status_fields
from core.dashboard_data import _surplus_for_payload, _reassign_status_after_pr


# ──────────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────────
def _base_lead(**overrides) -> Lead:
    """A minimal valid Lead; override fields per test."""
    defaults = dict(
        county_id="cuyahoga-oh", county_name="Cuyahoga", state="OH",
        case_number="CV-24-100000", address="123 Main St", parcel_id="P1",
        auction_type="foreclosure", opening_bid=50000.0,
        final_sale_price=120000.0, gross_surplus=70000.0, assessed_value=90000.0,
        sale_date="2026-05-10", sale_datetime="May 10, 2026 9:00 AM ET",
        sold_to="3rd Party Bidder", is_third_party=True,
        source_url="https://example.gov/case", auction_status="sold",
        scraped_at="2026-05-12T00:00:00", source_file="x.jsonl",
    )
    defaults.update(overrides)
    return Lead(**defaults)


def _to_payload(lead: Lead) -> dict:
    """Mirror the payload dict dashboard_data builds from a Lead."""
    return {
        "county_id": lead.county_id, "case_number": lead.case_number,
        "gross_surplus": lead.gross_surplus, "true_surplus": lead.true_surplus,
        "final_sale_price": lead.final_sale_price, "sale_date": lead.sale_date,
        "classification": lead.classification, "docket_url": lead.docket_url,
        "source_url": lead.source_url, "proof_of_surplus": lead.proof_of_surplus,
        "money_status": lead.money_status, "lead_quality": lead.lead_quality,
        "research_status": lead.research_status, "evidence_level": lead.evidence_level,
        "pipeline_ready": lead.pipeline_ready,
        "real_surplus_estimate": getattr(lead, "_pr_estimate", None),
        "pr_match": getattr(lead, "_pr_match", False),
    }


_results = []
def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    _results.append((name, condition))
    line = f"  [{status}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)


# ──────────────────────────────────────────────────────────────────────
# TEST 1 — classification unknown + true_surplus exists → NOT confirmed
# ──────────────────────────────────────────────────────────────────────
def test_1_unknown_classification_not_confirmed():
    lead = _base_lead(classification="unknown")
    lead.true_surplus = 40000.0          # a number IS present
    lead.proof_of_surplus = "doc"
    lead.docket_url = "https://example.gov/d"
    assign_status_fields(lead)
    check("T1: unknown classification is NOT confirmed_surplus",
          lead.money_status != "confirmed_surplus",
          f"money_status={lead.money_status}, pipeline_ready={lead.pipeline_ready}")
    check("T1: unknown classification not pipeline_ready",
          lead.pipeline_ready is False)


# ──────────────────────────────────────────────────────────────────────
# TEST 2 — classification killed + true_surplus > 0 → excluded, not ready
# ──────────────────────────────────────────────────────────────────────
def test_2_killed_excluded_from_confirmed():
    lead = _base_lead(classification="killed")
    lead.true_surplus = 22000.0          # positive surplus, but killed
    lead.kill_signals = ["motion_to_vacate"]
    assign_status_fields(lead)
    payload = _to_payload(lead)
    amount, bucket = _surplus_for_payload(payload)
    check("T2: killed lead is NOT confirmed_surplus",
          lead.money_status != "confirmed_surplus",
          f"money_status={lead.money_status}")
    check("T2: killed lead contributes $0 to confirmed bucket",
          bucket != "confirmed_surplus",
          f"bucket={bucket}, amount={amount}")
    check("T2: killed lead not pipeline_ready",
          lead.pipeline_ready is False)


# ──────────────────────────────────────────────────────────────────────
# TEST 3 — PropertyRadar match w/ estimate → property_enriched / estimated
# ──────────────────────────────────────────────────────────────────────
def test_3_propertyradar_is_estimated_not_confirmed():
    # Updated for FP-8: estimated_surplus requires PR TotalLoanBalance > $0.
    # A PR match with TLB > $0 means PR actually refined the surplus arithmetic
    # (sale price minus reported encumbrance). A PR match with TLB = $0 falls
    # to apparent_surplus per FP-8 — that's a separate test below.
    lead = _base_lead(classification="")     # no docket classification
    lead.true_surplus = None
    assign_status_fields(lead)               # loader: auction_only first
    payload = _to_payload(lead)
    payload["pr_match"] = True
    payload["pr_total_loan_balance"] = 220000.0  # FP-8: REAL refinement
    payload["real_surplus_estimate"] = 88000.0
    _reassign_status_after_pr(payload)        # PR merge re-tags it
    amount, bucket = _surplus_for_payload(payload)
    check("T3: PR match (TLB>0) → research_status property_enriched",
          payload["research_status"] == "property_enriched",
          f"research_status={payload['research_status']}")
    check("T3: PR match (TLB>0) → money_status estimated_surplus",
          payload["money_status"] == "estimated_surplus")
    check("T3: PR match (TLB>0) is NOT confirmed_surplus",
          bucket != "confirmed_surplus", f"bucket={bucket}")


# ──────────────────────────────────────────────────────────────────────
# TEST 4 — auction-only gross surplus → auction_only / apparent
# ──────────────────────────────────────────────────────────────────────
def test_4_auction_only_is_apparent():
    lead = _base_lead(classification="")     # no docket, no PR
    lead.true_surplus = None
    assign_status_fields(lead)
    payload = _to_payload(lead)
    amount, bucket = _surplus_for_payload(payload)
    check("T4: auction-only → research_status auction_only",
          lead.research_status == "auction_only",
          f"research_status={lead.research_status}")
    check("T4: auction-only → money_status apparent_surplus",
          lead.money_status == "apparent_surplus")
    check("T4: auction-only is NOT confirmed_surplus",
          bucket != "confirmed_surplus", f"bucket={bucket}")
    check("T4: auction-only not pipeline_ready",
          lead.pipeline_ready is False)


# ──────────────────────────────────────────────────────────────────────
# TEST 5 — green + all proof fields → confirmed_surplus / pipeline_ready
# ──────────────────────────────────────────────────────────────────────
def test_5_green_with_full_proof_is_confirmed():
    lead = _base_lead(classification="green")
    lead.prayer_amount = 80000.0
    lead.true_surplus = 40000.0              # final 120k - prayer 80k
    lead.docket_url = "https://example.gov/docket/CV-24-100000"
    lead.proof_of_surplus = "Final Judgment of Foreclosure filed 2026-05-01"
    assign_status_fields(lead)
    payload = _to_payload(lead)
    amount, bucket = _surplus_for_payload(payload)
    check("T5: green + full proof → money_status confirmed_surplus",
          lead.money_status == "confirmed_surplus",
          f"money_status={lead.money_status}")
    check("T5: green + full proof → pipeline_ready True",
          lead.pipeline_ready is True)
    check("T5: green + full proof → bucket confirmed_surplus, amount 40000",
          bucket == "confirmed_surplus" and amount == 40000.0,
          f"bucket={bucket}, amount={amount}")
    check("T5: green + full proof → evidence_level docket_confirmed",
          lead.evidence_level == "docket_confirmed")


# ──────────────────────────────────────────────────────────────────────
# TEST 6 — green but missing proof_of_surplus → downgraded, not confirmed
# ──────────────────────────────────────────────────────────────────────
def test_6_green_missing_proof_is_downgraded():
    lead = _base_lead(classification="green")
    lead.prayer_amount = 80000.0
    lead.true_surplus = 40000.0
    lead.docket_url = "https://example.gov/docket/CV-24-100000"
    lead.proof_of_surplus = ""               # ← MISSING required proof field
    assign_status_fields(lead)
    payload = _to_payload(lead)
    amount, bucket = _surplus_for_payload(payload)
    check("T6: green missing proof_of_surplus → NOT confirmed_surplus",
          lead.money_status != "confirmed_surplus",
          f"money_status={lead.money_status}")
    check("T6: green missing proof → not pipeline_ready",
          lead.pipeline_ready is False)
    check("T6: green missing proof → bucket not confirmed",
          bucket != "confirmed_surplus", f"bucket={bucket}")


# ──────────────────────────────────────────────────────────────────────
# TEST 7 — red classification w/ surplus amount → not ready, not counted
# ──────────────────────────────────────────────────────────────────────
def test_7_red_not_ready_not_counted():
    lead = _base_lead(classification="red")
    lead.true_surplus = 55000.0              # has a surplus number
    lead.competing_filers = ["Assignee LLC"]
    assign_status_fields(lead)
    payload = _to_payload(lead)
    amount, bucket = _surplus_for_payload(payload)
    check("T7: red lead not pipeline_ready",
          lead.pipeline_ready is False,
          f"pipeline_ready={lead.pipeline_ready}")
    check("T7: red lead NOT in confirmed bucket",
          bucket != "confirmed_surplus", f"bucket={bucket}")
    check("T7: red lead money_status is not confirmed_surplus",
          lead.money_status != "confirmed_surplus",
          f"money_status={lead.money_status}")


# ──────────────────────────────────────────────────────────────────────
# TEST 8 — FP-8: PR match with TLB=$0 must DROP to apparent (no tier inflation)
# ──────────────────────────────────────────────────────────────────────
def test_8_pr_match_zero_tlb_drops_to_apparent():
    lead = _base_lead(classification="")
    lead.true_surplus = None
    assign_status_fields(lead)
    payload = _to_payload(lead)
    payload["pr_match"] = True
    payload["pr_total_loan_balance"] = 0.0       # FP-8: NO real refinement
    payload["real_surplus_estimate"] = payload["gross_surplus"]
    _reassign_status_after_pr(payload)
    amount, bucket = _surplus_for_payload(payload)
    check("T8: PR match + TLB=$0 → money_status apparent_surplus",
          payload["money_status"] == "apparent_surplus",
          f"money_status={payload['money_status']}")
    check("T8: PR match + TLB=$0 → bucket apparent_surplus",
          bucket == "apparent_surplus", f"bucket={bucket}")
    check("T8: PR match + TLB=$0 → pr_match field stays True (intel preserved)",
          payload["pr_match"] is True)


# ──────────────────────────────────────────────────────────────────────
# TEST 9 — FP-10: FL lead with no docket gets fl_opening_bid debt_source
# and stays apparent_surplus (NOT a new tier, NOT confirmed)
# ──────────────────────────────────────────────────────────────────────
def test_9_fl_opening_bid_debt_source():
    from core.loader import _parse_lead
    fl_lead = _parse_lead({
        "opening_bid": 100000,
        "final_sale_price": 175000,
        "case_number": "FL-TEST-9",
        "address": "999 OCEAN BLVD",
        "is_third_party": True,
    }, "miami-dade-fl", "test.jsonl")
    assign_status_fields(fl_lead)
    check("T9: FL lead with opening_bid + sale → true_surplus = sale - opening",
          fl_lead.true_surplus == 75000.0,
          f"true_surplus={fl_lead.true_surplus}")
    check("T9: FL lead → debt_source = 'fl_opening_bid'",
          fl_lead.debt_source == "fl_opening_bid",
          f"debt_source={fl_lead.debt_source!r}")
    check("T9: FL lead with no docket → money_status apparent_surplus (NOT confirmed, NOT estimated)",
          fl_lead.money_status == "apparent_surplus",
          f"money_status={fl_lead.money_status}")
    check("T9: FL lead with no docket → pipeline_ready False (needs kill-signal check)",
          fl_lead.pipeline_ready is False)


# ──────────────────────────────────────────────────────────────────────
# TEST 10 — FP-10: OH lead opening_bid stays fake; no fl rule applied
# ──────────────────────────────────────────────────────────────────────
def test_10_oh_opening_bid_stays_fake():
    from core.loader import _parse_lead
    oh_lead = _parse_lead({
        "opening_bid": 30000,   # statutory 2/3-appraised, NOT real debt
        "final_sale_price": 72000,
        "case_number": "CV-OH-TEST-10",
        "is_third_party": True,
    }, "summit-oh", "test.jsonl")
    assign_status_fields(oh_lead)
    check("T10: OH lead → true_surplus stays None (opening_bid is fake)",
          oh_lead.true_surplus is None,
          f"true_surplus={oh_lead.true_surplus}")
    check("T10: OH lead → debt_source empty (no docket prayer yet)",
          oh_lead.debt_source == "",
          f"debt_source={oh_lead.debt_source!r}")
    check("T10: OH lead with no docket → money_status apparent_surplus",
          oh_lead.money_status == "apparent_surplus")


# ──────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  SurplusIQ — Verification Hardening Test Suite")
    print("=" * 70)
    for fn in [
        test_1_unknown_classification_not_confirmed,
        test_2_killed_excluded_from_confirmed,
        test_3_propertyradar_is_estimated_not_confirmed,
        test_4_auction_only_is_apparent,
        test_5_green_with_full_proof_is_confirmed,
        test_6_green_missing_proof_is_downgraded,
        test_7_red_not_ready_not_counted,
        test_8_pr_match_zero_tlb_drops_to_apparent,
        test_9_fl_opening_bid_debt_source,
        test_10_oh_opening_bid_stays_fake,
    ]:
        print(f"\n{fn.__name__}")
        fn()

    passed = sum(1 for _, ok in _results if ok)
    total = len(_results)
    print("\n" + "=" * 70)
    print(f"  RESULT: {passed}/{total} checks passed")
    print("=" * 70)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
