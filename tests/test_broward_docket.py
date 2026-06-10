"""
SurplusIQ — Broward docket classifier acceptance test (standalone, no pytest).

    python -m tests.test_broward_docket

Network-free: drives core.dockets.broward.parse_docket / _apply_evidence_level
over REAL committed docket JSON (data/samples/broward/ci/<CASE>_docket.json,
captured by Actions run 27312374787 — see SURPLUS_VOCAB_FINDINGS.md). This is the
exact Phase-2 path the live scraper runs after fetch_docket().

Acceptance (build spec):
  * the 9 ground-truthed surplus-claim cases MUST classify killed
  * the 3 traps — homeowner (Merritt), purchaser (NASINNYA), HOA (Manor Grove) —
    MUST NOT be flagged recovery firms; the HOA-only case MUST stay pursuable
  * clean cases MUST stay pursuable (not killed)
"""
from __future__ import annotations
import json
import glob
import os
import sys

from core.dockets.base import DocketResult
from core.dockets.broward import (
    BrowardDocketScraper,
    classify_appearance,
    collect_party_and_purchaser_names,
    NOA_BENIGN,
    NOA_RECOVERY_KILL,
)

SAMPLE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "samples", "broward", "ci")
)

# 9 ground-truthed surplus-claim cases (SURPLUS_VOCAB_FINDINGS.md) — must KILL.
CONFIRMED_KILL = [
    "CACE-24-008631", "CACE-25-005168", "CACE-24-012541", "CACE-23-015282",
    "CACE-25-002358", "CONO-25-048381", "CACE-25-004451", "COCE-25-085528",
    "CACE-24-007420",
]

KNOWN_FIRMS = [
    "GET LIQUID FUNDING, LLC", "PRIORITY SURPLUS LLC", "The Recovery Agents, LLC",
    "AMERIFUND EQUITY GROUP", "New Beginnings Trustee, LLC as Assignee for J. Roux",
    "Capital Crafter Inc.",
]
BENIGN_APPEARANCES = [
    "Party: Plaintiff Nationstar Mortgage Llc",
    "Party: Defendant Karagic, Muhamed",
    "ANTHONY J. ALONEFTIS, ESQ.",
    "JUAN C MARTINEZ AS COUNSEL FOR CANBY BUSINESS PARK, LLC (PURCHASER)",
]

_PASS, _FAIL = 0, 0


def _check(cond: bool, msg: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {msg}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {msg}")


def _rows(case_nodash: str) -> list:
    with open(os.path.join(SAMPLE_DIR, f"{case_nodash}_docket.json"), encoding="utf-8") as f:
        return json.load(f)["rows"]


def _classify(case: str) -> DocketResult:
    s = BrowardDocketScraper()
    r = DocketResult(county_id="broward-fl", case_number=case)
    s.parse_docket(_rows(case.replace("-", "")), r)
    s._apply_evidence_level(r)
    return r


def _appearance(case: str, name: str) -> str:
    party, purchaser = collect_party_and_purchaser_names(_rows(case.replace("-", "")))
    return classify_appearance(name, party, purchaser)[0]


def main() -> int:
    print("=" * 70)
    print("Broward docket classifier — acceptance against 22 real cases")
    print("=" * 70)

    print("\nfull 22-case classification")
    for path in sorted(glob.glob(os.path.join(SAMPLE_DIR, "*_docket.json"))):
        case = json.load(open(path, encoding="utf-8"))["case"]
        r = _classify(case)
        print(f"    {case:<16} {r.classification:<8} {r.lead_status:<22} "
              f"{r.classification_reason[:64]}")

    print("\n1. the 9 ground-truthed surplus-claim cases must KILL")
    for case in CONFIRMED_KILL:
        _check(_classify(case).classification == "killed", f"{case} killed")

    print("\n2. traps must NOT cause a wrong kill")
    _check(_appearance("CACE-24-012541", "RANDOLPH MERRITT") == NOA_BENIGN,
           "homeowner Merritt (pro se) classified benign")
    _check(_appearance("COWE-26-005819", "NASINNYA LLC") != NOA_RECOVERY_KILL,
           "auction purchaser NASINNYA not a recovery kill")
    _check(_appearance("CACE-25-009885", "MANOR GROVE VILLAGE ONE, INC.") == NOA_BENIGN,
           "HOA Manor Grove classified benign")
    _check(_classify("CACE-25-009885").classification != "killed",
           "HOA-only case CACE-25-009885 stays pursuable")

    print("\n3. known recovery firms must KILL when residual")
    for firm in KNOWN_FIRMS:
        _check(classify_appearance(firm, set(), set())[0] == NOA_RECOVERY_KILL,
               f"recovery firm killed: {firm[:40]}")

    print("\n4. benign party/counsel appearances must NOT kill")
    for ap in BENIGN_APPEARANCES:
        _check(classify_appearance(ap, set(), set())[0] == NOA_BENIGN,
               f"benign: {ap[:45]}")

    print("\n5. clean sold cases must stay pursuable (not killed)")
    for case in ["CACE-21-019437", "CACE-25-003189", "COCE-25-009070"]:
        _check(_classify(case).classification in {"green", "yellow"},
               f"{case} pursuable")

    print("\n" + "=" * 70)
    print(f"  RESULT: {_PASS}/{_PASS + _FAIL} checks passed")
    print("=" * 70)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
