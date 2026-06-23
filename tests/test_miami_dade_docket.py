"""
SurplusIQ — Miami-Dade docket classifier acceptance test (standalone, no pytest).

    python -m tests.test_miami_dade_docket

Network-free: drives core.dockets.miami_dade.parse_docket / _apply_evidence_level
over REAL captured Case-Information HTML (data/samples/miami_dade/cases/<TAG>.html,
captured by the Actions miami-capture probe). This is the EXACT Phase-2 path the
live scraper runs after fetch_docket_html() — same rigor as Broward/Duval.

Acceptance (the cron gate — proves the kill path on REAL Miami cases):
  * a real surplus-claim case MUST kill, citing the docket claim
  * cancelled/vacated-sale cases MUST kill (sale_issue, ranks above bankruptcy)
  * the resolved-bankruptcy case MUST be caution, NOT killed (the fixed false-kill)
  * clean cases stay pursuable
"""
from __future__ import annotations
import os
import re
import sys

from core.dockets.base import DocketResult
from core.dockets.miami_dade import MiamiDadeDocketScraper

CASES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                     "data", "samples", "miami_dade", "cases"))
_PASS, _FAIL = 0, 0


def _check(cond: bool, msg: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {msg}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {msg}")


def _classify(case: str) -> DocketResult:
    tag = re.sub(r"[^A-Za-z0-9]", "", case).upper()
    html = open(os.path.join(CASES, f"{tag}.html"), encoding="utf-8").read()
    s = MiamiDadeDocketScraper()
    r = DocketResult(county_id="miami-dade-fl", case_number=case)
    s.parse_docket(html, r)
    s._apply_evidence_level(r)
    return r


def main() -> int:
    print("=" * 72)
    print("Miami-Dade docket classifier — acceptance against real captured HTML")
    print("=" * 72)

    print("\n1. real surplus-claim case MUST kill, citing the docket claim")
    r = _classify("2024-020538-CA-01")
    _check(r.classification == "killed", f"2024-020538 killed ({r.classification})")
    _check(r.evidence_level == "claim_filed", f"2024-020538 evidence=claim_filed ({r.evidence_level})")
    _check("surplus" in r.classification_reason.lower(),
           f"2024-020538 reason cites surplus claim: {r.classification_reason[:70]!r}")

    print("\n2. cancelled / vacated sale cases MUST kill (sale_issue)")
    for case in ["2025-000672-CA-01", "2020-000151-CA-01", "2023-001008-CA-01"]:
        r = _classify(case)
        _check(r.classification == "killed" and r.evidence_level == "sale_issue_found",
               f"{case} killed/sale_issue ({r.classification}/{r.evidence_level}) — {r.classification_reason[:55]}")

    print("\n3. resolved-bankruptcy case → caution, NOT killed (the fixed false-kill)")
    r = _classify("2017-021344-CA-01")
    _check(r.classification != "killed", f"2017-021344 NOT killed ({r.classification})")
    _check(r.lead_status == "pursuable_with_caution" and r.evidence_level == "bankruptcy_found",
           f"2017-021344 caution/bankruptcy_found ({r.lead_status}/{r.evidence_level})")

    print("\n4. clean cases stay pursuable")
    for case in ["2019-009163-CC-05", "2025-095651-CC-25"]:
        r = _classify(case)
        _check(r.classification in {"green", "yellow"} and r.lead_status == "pursuable",
               f"{case} pursuable ({r.classification}/{r.lead_status})")

    print("\n   full classification of all sample cases")
    for case in ["2024-020538-CA-01", "2025-000672-CA-01", "2020-000151-CA-01",
                 "2023-001008-CA-01", "2017-021344-CA-01", "2019-009163-CC-05",
                 "2025-095651-CC-25"]:
        r = _classify(case)
        print(f"     {case:<20} {r.classification:<7} {r.lead_status:<22} {r.classification_reason[:55]}")

    print("\n" + "=" * 72)
    print(f"  RESULT: {_PASS}/{_PASS + _FAIL} checks passed")
    print("=" * 72)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
