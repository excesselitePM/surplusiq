"""
SurplusIQ — Duval docket classifier acceptance test (standalone, no pytest).

    python -m tests.test_duval_docket

Network-free: drives core.dockets.duval.parse_docket / _apply_evidence_level over
REAL committed case-detail text (data/samples/duval/{ci,party}/*.txt, captured by
the Actions investigation probe). This is the EXACT Phase-2 path the live scraper
runs after fetch_docket() — parse_case_text() consumes the same innerText.

Acceptance (build spec — the cron gate):
  * real Surplus Refund Corp / Surplus Return Group cases MUST kill, and ALL THREE
    cross-confirming signals (3rd-party / fee-code / text) MUST fire on them
  * clean recently-sold cases stay pursuable (incl. pre-title vacate/cancel churn)
  * the "VALUE OF REAL PROPERTY OR MORTGAGE FORECLOSURE CLAIM" cover sheet must NOT fire
  * a benign NOA (Bank of America / HOA counsel) is NOT a kill
  * surplus-BALANCE extraction matches the real captured values
"""
from __future__ import annotations
import os
import sys

from core.dockets.base import DocketResult
from core.dockets.duval import (
    DuvalDocketScraper, parse_case_text, classify_noa,
    detect_surplus_party, detect_surplus_fee, detect_surplus_text,
    extract_surplus_balance,
)

SAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "samples", "duval"))
_PASS, _FAIL = 0, 0


def _check(cond: bool, msg: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {msg}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {msg}")


def _load(tag: str) -> str:
    for sub, suffix in (("party", "_docket.txt"), ("ci", "_D_detail.txt")):
        p = os.path.join(SAMPLES, sub, f"{tag}{suffix}")
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
    raise FileNotFoundError(tag)


def _classify(tag: str) -> DocketResult:
    s = DuvalDocketScraper()
    r = DocketResult(county_id="duval-fl", case_number=tag)
    s.parse_docket(_load(tag), r)
    s._apply_evidence_level(r)
    return r


# Real claimed cases (party-search). Surplus Refund Corp / Surplus Return Group.
CLAIM_CASES = ["2016CA002623XXXX", "2016CA006997XXXX", "2017CA000908XXXX", "2017CA003824XXXX"]
# Clean recently-sold cases (auction sample).
CLEAN_CASES = ["162025CA005932AXXXMA", "162025CA006213AXXXMA",
               "162025CA006229AXXXMA", "162025CA004483AXXXMA"]


def main() -> int:
    print("=" * 72)
    print("Duval docket classifier — acceptance against real captured cases")
    print("=" * 72)

    print("\n1. real surplus-claim cases MUST kill — and all THREE signals fire")
    for tag in CLAIM_CASES:
        r = _classify(tag)
        ev = getattr(r, "_evidence", {})
        _check(r.classification == "killed", f"{tag} killed")
        _check(bool(ev.get("claim_party")), f"{tag} signal 1: 3rd-party claimant ({ev.get('claim_party','')[:40]})")
        _check(bool(ev.get("claim_fee")), f"{tag} signal 2: surplus fee code ({ev.get('claim_fee','')[:30]})")
        _check(bool(ev.get("claim_text")), f"{tag} signal 3: surplus text ({ev.get('claim_text','')[:40]})")

    print("\n2. clean recently-sold cases stay pursuable (NOT killed)")
    for tag in CLEAN_CASES:
        r = _classify(tag)
        _check(r.classification in {"green", "yellow"}, f"{tag} pursuable ({r.classification})")

    print("\n3. cover-sheet 'CLAIM' must NOT fire as a surplus claim")
    cover = [("9/24/2025", "VALUE OF REAL PROPERTY OR MORTGAGE FORECLOSURE CLAIM")]
    _check(detect_surplus_text(cover) == "", "cover-sheet 'FORECLOSURE CLAIM' not a surplus claim")
    # and the routine plaintiff payoff cert must NOT fire
    payoff = [("6/9/2026", "CERTIFICATE OF FORECLOSURE DISBURSEMENT $55,596.27 TO: U.S. BANK, NA BALANCE: $42403.73")]
    _check(detect_surplus_text(payoff) == "", "routine plaintiff payoff disbursement not a surplus claim")

    print("\n4. NOA 'OF COUNSEL ... FOR <party>' — recovery kills, benign does not")
    _check(classify_noa([("", "NOTICE OF APPEARANCE OF COUNSEL STEVEN IMPARATO FOR SURPLUS REFUND CORP.")])[0]
           == "recovery", "recovery NOA (FOR SURPLUS REFUND CORP) -> recovery")
    _check(classify_noa([("", "NOTICE OF APPEARANCE OF COUNSEL JUSTIN MOOREFIELD FOR SURPLUS RETURN GROUP")])[0]
           == "recovery", "recovery NOA (FOR SURPLUS RETURN GROUP) -> recovery")
    _check(classify_noa([("", "NOTICE OF APPEARANCE OF COUNSEL SHAIB RIOS FOR BANK OF AMERICA, N.A.")])[0]
           == "benign", "benign NOA (FOR BANK OF AMERICA) -> benign, not a kill")
    _check(classify_noa([("", "NOTICE OF APPEARANCE OF COUNSEL MICHAEL MCCABE FOR SOUTHERN GROVE CONDOMINIUM ASSOCIATION, INC.")])[0]
           == "benign", "benign NOA (FOR HOA) -> benign, not a kill")

    print("\n5. surplus-BALANCE extraction matches the real captured values")
    b1 = extract_surplus_balance(parse_case_text(_load("162025CA005932AXXXMA"))["dockets"])
    _check(abs(b1 - 42403.73) < 0.01, f"005932 surplus balance = ${b1:,.2f} (want $42,403.73)")
    b2 = extract_surplus_balance(parse_case_text(_load("162023CA010844XXXXMA"))["dockets"])
    _check(abs(b2 - 29609.15) < 0.01, f"010844 surplus balance = ${b2:,.2f} (want $29,609.15)")

    print("\n6. bankruptcy is a FLAG (yellow), never a kill")
    r = _classify("162023CA010844XXXXMA")   # has SUGGESTION OF BANKRUPTCY ×2, no claim
    _check(r.classification == "yellow" and r.lead_status == "pursuable_with_caution",
           f"010844 bankruptcy -> yellow/caution ({r.classification})")

    print("\n   full classification of all sample cases")
    for tag in CLAIM_CASES + CLEAN_CASES + ["162023CA010844XXXXMA"]:
        r = _classify(tag)
        print(f"     {tag:<22} {r.classification:<7} {r.lead_status:<22} {r.classification_reason[:60]}")

    print("\n" + "=" * 72)
    print(f"  RESULT: {_PASS}/{_PASS + _FAIL} checks passed")
    print("=" * 72)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
