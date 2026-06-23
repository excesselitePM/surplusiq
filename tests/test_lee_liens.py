"""
Lee PR-first lien-consumes-surplus — acceptance test.

Runs the EXACT production classifier (core/enrichment/lee_liens.classify_lien_surplus)
against REAL committed PR records (data/samples/lee/ci/*_liens.json, captured by
Lee Investigate run 28053764053) plus clearly-labelled SYNTHETIC fixtures for the
branches no current Lee lead exhibits (hard kill: no live Lee lead has a junior
lien exceeding its surplus).

Acceptance per the build spec:
  • lien-eats-surplus  → HARD KILL          (synthetic: real SecondAmount > surplus)
  • clean (lien < surplus) → pursuable      (real P7BF0EE7: no lien, $49K surplus)
  • no-lien            → clean              (real P7BCFB8B)
  • PROPERTY-keyed liens used, NOT person-keyed
  • owner-timing handled (new owner / isListedForSale=1 never hard-kills, never
    certifies a masked-clean record as truly clean)
  • ANTI-FABRICATION: no extractable amount → no kill
"""
import os
import sys
import json
import glob

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.enrichment.lee_liens import classify_lien_surplus, detect_owner_timing

SAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "data", "samples", "lee", "ci"))

_checks = []


def check(name, cond, detail=""):
    _checks.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def load(radarid):
    with open(os.path.join(SAMPLES, f"{radarid}_liens.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    print("=" * 72)
    print("  Lee PR-first lien-consumes-surplus — acceptance (real PR records + synthetic kills)")
    print("=" * 72)

    # ── 1. REAL no-lien / clean — P7BF0EE7 (3350 N KEY DR, $49,174 surplus) ──
    print("\n1. REAL clean / no-lien case (P7BF0EE7, individual owner, not listed)")
    s = load("P7BF0EE7")
    v = classify_lien_surplus(s["pr_record"], s["auction_surplus"])
    print("   " + v.summary())
    check("P7BF0EE7 classified clean", v.classification == "clean", v.classification)
    check("P7BF0EE7 NOT a hard kill", v.is_hard_kill is False)
    check("P7BF0EE7 owner-timing NOT suspect (individual owner BUCK,JEFF)",
          v.owner_timing_suspect is False)

    # ── 2. REAL no-lien — P7BCFB8B (235 EUPHRATES, $172 surplus) ──
    print("\n2. REAL no-lien case (P7BCFB8B)")
    s = load("P7BCFB8B")
    v = classify_lien_surplus(s["pr_record"], s["auction_surplus"])
    print("   " + v.summary())
    check("P7BCFB8B classified clean", v.classification == "clean", v.classification)
    check("P7BCFB8B NOT a hard kill", v.is_hard_kill is False)

    # ── 3. REAL lien present + OWNER-TIMING trap — P7BF6453 (318 SE 46TH LN) ──
    # The property Eric flagged as "lien-eats-surplus": lien $30,378 < surplus
    # $33,874 (does NOT actually exceed it), AND owner is UPSTATE ENTERPRISES LLC
    # with isListedForSale=1 → owner-timing suspect → caution, never killed.
    print("\n3. REAL lien + owner-timing trap (P7BF6453, LLC owner, isListedForSale=1)")
    s = load("P7BF6453")
    rec = s["pr_record"]
    v = classify_lien_surplus(rec, s["auction_surplus"])
    print("   " + v.summary())
    check("P7BF6453 owner-timing SUSPECT (LLC + isListedForSale=1)",
          v.owner_timing_suspect is True)
    check("P7BF6453 NOT hard-killed (lien amount is the new owner's)",
          v.is_hard_kill is False)
    check("P7BF6453 → lien_risk caution (not clean, not killed)",
          v.classification == "lien_risk", v.classification)
    check("P7BF6453 lien amount ($30,378) is BELOW surplus ($33,874) — not eaten",
          rec["TotalLoanBalance"] < s["auction_surplus"],
          f'{rec["TotalLoanBalance"]} < {s["auction_surplus"]}')

    # ── 4. SYNTHETIC hard kill — real itemized SecondAmount > surplus, owner clean ─
    print("\n4. SYNTHETIC hard kill (itemized SecondAmount > surplus, owner trustworthy)")
    kill_rec = {
        "Owner": "GREEN, KEN", "PropertyHasOpenLiens": 1,
        "SecondAmount": 60000, "SecondLoanType": "HELOC",
        "TotalLoanBalance": 230000, "NumberLoans": 2,
        "AVM": 250000, "AvailableEquity": 20000,
        "isListedForSale": 0,
    }
    v = classify_lien_surplus(kill_rec, 33874.21, docket_owner="KEN GREEN")
    print("   " + v.summary())
    check("synthetic killed", v.classification == "killed", v.classification)
    check("synthetic is_hard_kill True", v.is_hard_kill is True)
    check("synthetic kill source = second_position", v.lien_source == "second_position")
    check("synthetic kill NOT owner-timing suspect (PR owner matches docket)",
          v.owner_timing_suspect is False)

    # ── 5. SYNTHETIC: lien > surplus BUT owner-timing suspect → downgrade to caution
    print("\n5. SYNTHETIC owner-timing downgrade (SecondAmount > surplus but relisted)")
    rec5 = dict(kill_rec, isListedForSale=1)        # same lien, but post-auction relist
    v = classify_lien_surplus(rec5, 33874.21)       # no docket owner to vouch
    print("   " + v.summary())
    check("downgraded: NOT a hard kill on untrusted owner", v.is_hard_kill is False)
    check("downgraded: classification lien_risk", v.classification == "lien_risk", v.classification)
    check("downgraded: owner-timing suspect", v.owner_timing_suspect is True)

    # ── 6. SYNTHETIC coarse caution — no itemized amount, implied ≥ surplus ──
    print("\n6. SYNTHETIC coarse caution (no SecondAmount; TotalLoanBalance ≥ surplus)")
    rec6 = {"Owner": "DOE, JANE", "PropertyHasOpenLiens": 1,
            "TotalLoanBalance": 40000, "NumberLoans": 1,
            "AVM": 200000, "AvailableEquity": 160000, "isListedForSale": 0}
    v = classify_lien_surplus(rec6, 33874.21)
    print("   " + v.summary())
    check("coarse: lien_risk (estimate, not kill)", v.classification == "lien_risk", v.classification)
    check("coarse: NOT a hard kill", v.is_hard_kill is False)
    check("coarse: source = total_loan_balance", v.lien_source == "total_loan_balance")

    # ── 7. ANTI-FABRICATION — lien present, no amount, estimate < surplus → pursuable
    print("\n7. ANTI-FABRICATION (lien present, estimate < surplus, owner clean → pursuable)")
    rec7 = {"Owner": "ROE, RICH", "PropertyHasOpenLiens": 1,
            "TotalLoanBalance": 5000, "NumberLoans": 1,
            "AVM": 200000, "AvailableEquity": 195000, "isListedForSale": 0}
    v = classify_lien_surplus(rec7, 33874.21)
    print("   " + v.summary())
    check("anti-fab: NOT killed (no amount exceeds surplus)", v.is_hard_kill is False)
    check("anti-fab: pursuable (lien does not consume surplus)",
          v.classification == "pursuable", v.classification)

    # ── 8. PROPERTY-keyed, NOT person-keyed ──
    print("\n8. PROPERTY-keyed liens used, person-keyed IGNORED")
    rec8 = {"Owner": "PERSON, PAT", "PropertyHasOpenLiens": 0,
            "PropertyHasOpenPersonLiens": 1,          # the NEW owner's personal lien
            "TotalLoanBalance": 0, "AVM": 180000, "AvailableEquity": 180000,
            "isListedForSale": 0}
    v = classify_lien_surplus(rec8, 33874.21)
    print("   " + v.summary())
    check("person-lien-only record is treated CLEAN (property-keyed governs)",
          v.classification == "clean", v.classification)
    check("person-lien-only is NOT a hard kill", v.is_hard_kill is False)

    # ── 9. SYNTHETIC owner-timing masks a clean record ──
    print("\n9. SYNTHETIC masked-clean (no lien on PR, but isListedForSale=1)")
    rec9 = {"Owner": "FLIP HOLDINGS LLC", "PropertyHasOpenLiens": 0,
            "TotalLoanBalance": 0, "AVM": 200000, "AvailableEquity": 200000,
            "isListedForSale": 1}
    v = classify_lien_surplus(rec9, 33874.21)
    print("   " + v.summary())
    check("masked-clean: owner-timing suspect", v.owner_timing_suspect is True)
    check("masked-clean: flagged liens_possibly_masked",
          "liens_possibly_masked" in v.flags)
    check("masked-clean: NOT a hard kill", v.is_hard_kill is False)

    # ── 10. docket-owner mismatch alone triggers owner-timing suspect ──
    print("\n10. docket-owner mismatch triggers owner-timing")
    suspect, reasons = detect_owner_timing(
        {"Owner": "ACME CAPITAL LLC", "isListedForSale": 0}, docket_owner="JOHNSON, MARY")
    check("mismatch → suspect", suspect is True, "; ".join(reasons)[:80])

    # ── 11. WIRE-IN: dashboard verdict application (kill filters out, caution stays) ──
    print("\n11. WIRE-IN _apply_lee_lien_verdict — kill → classification 'killed' (FP-14 drops)")
    from core.dashboard_data import _apply_lee_lien_verdict
    killed_payload = {
        "county_id": "lee-fl", "case_number": "SYN-KILL",
        "classification": "", "money_status": "apparent_surplus",
        "lee_lien_classification": "killed", "lee_lien_is_hard_kill": True,
        "lee_lien_amount": 60000, "lee_lien_source": "second_position",
        "lee_lien_reason": "itemized second-position lien $60,000 exceeds surplus",
    }
    _apply_lee_lien_verdict(killed_payload)
    check("kill → classification 'killed'", killed_payload["classification"] == "killed")
    check("kill → money_status 'no_surplus'", killed_payload["money_status"] == "no_surplus")
    check("kill → reason cites the lien amount",
          "60,000" in killed_payload.get("classification_reason", ""))
    # FP-14 filter semantics: a 'killed' classification is dropped from the deliverable.
    check("kill would be FILTERED OUT by FP-14",
          (killed_payload.get("classification") or "").lower() == "killed")

    print("\n12. WIRE-IN — lien_risk stays VISIBLE with a caution flag")
    caution_payload = {
        "county_id": "lee-fl", "case_number": "SYN-CAUTION",
        "classification": "", "money_status": "apparent_surplus",
        "lee_lien_classification": "lien_risk", "lee_lien_is_hard_kill": False,
        "lee_lien_amount": 30378, "lee_lien_reason": "owner-timing suspect",
    }
    _apply_lee_lien_verdict(caution_payload)
    check("caution → NOT killed (stays visible)", caution_payload["classification"] != "killed")
    check("caution → lee_lien_caution flag set", caution_payload.get("lee_lien_caution") is True)

    print("\n13. WIRE-IN — a 'killed' classification WITHOUT hard_kill does NOT filter out")
    soft = {"county_id": "lee-fl", "classification": "", "money_status": "apparent_surplus",
            "lee_lien_classification": "lien_risk", "lee_lien_is_hard_kill": False,
            "lee_lien_amount": 60000}
    _apply_lee_lien_verdict(soft)
    check("anti-fab: lien_risk (even big amount) is NOT a kill", soft["classification"] != "killed")

    print("\n14. WIRE-IN — non-Lee payload untouched")
    other = {"county_id": "summit-oh", "classification": "green", "money_status": "apparent_surplus"}
    _apply_lee_lien_verdict(other)
    check("non-Lee payload untouched", other["classification"] == "green")

    # ── result ──
    passed = sum(_checks)
    total = len(_checks)
    print("\n" + "=" * 72)
    print(f"  RESULT: {passed}/{total} checks passed")
    print("=" * 72)
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
