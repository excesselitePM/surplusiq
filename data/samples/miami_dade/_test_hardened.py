"""Hardened-detector verification — runs parse_docket + _apply_evidence_level
on the SAVED robust_*.html (deterministic, no network) and a phrase-breadth
unit test on the exact variants raised in review.

Run: PYTHONPATH=. python data/samples/miami_dade/_test_hardened.py
"""
import re
from pathlib import Path

from core.dockets.base import DocketResult
from core.dockets.miami_dade import (
    MiamiDadeDocketScraper,
    CLAIM_FILED_PATTERNS, SALE_ISSUE_PATTERNS, SALE_ISSUE_DENIAL_GUARD,
    BANKRUPTCY_ACTIVE_PATTERNS, BANKRUPTCY_RESOLVED_PATTERNS,
)

OUT = Path("data/samples/miami_dade")
S = MiamiDadeDocketScraper(headless=True)

EXPECT = {
    "2025-010668-CA-01": ("no_claim_found", "pursuable"),
    "2024-020538-CA-01": ("claim_filed", "not_pursuable"),
    # NEW RULES: these two have a sale-cancellation -> killed for SALE (Rule 2),
    # NOT for bankruptcy (Rule 1 = bankruptcy never kills).
    "2025-000672-CA-01": ("sale_issue_found", "not_pursuable"),
    "2019-001371-CA-01": ("sale_issue_found", "not_pursuable"),
    # baseline: bankruptcy (resolved) and NO sale issue -> caution, visible.
    "2017-021344-CA-01": ("bankruptcy_found", "pursuable_with_caution"),
}

print("==== classification on saved dockets ====")
allok = True
for case, (exp_ev, exp_ls) in EXPECT.items():
    h = (OUT / f"robust_{case}.html").read_text(encoding="utf-8")
    r = DocketResult(county_id="miami-dade-fl", case_number=case)
    S.parse_docket(h, r)
    S._apply_evidence_level(r)
    ok = (r.evidence_level == exp_ev and r.lead_status == exp_ls)
    allok &= ok
    print(f"\n{case}: {'OK ' if ok else 'XX '}"
          f"evidence={r.evidence_level} lead={r.lead_status} class={r.classification}")
    print(f"   expected: {exp_ev}/{exp_ls}")
    print(f"   reason  : {r.classification_reason}")
    print(f"   kill_signals={r.kill_signals} claim_filed={r.claim_filed}")

print("\n\n==== phrase-breadth unit test (review variants) ====")
def first(title, pats, exclude=None):
    return S._match_titles([title], pats, exclude)

CASES = [
    # (title, which detector, should_match)
    ("Owner's Claim for Surplus", "claim", True),
    ("Owners Claim for Mortgage Foreclosure Surplus", "claim", True),
    ("Homeowner's Claim for Surplus", "claim", True),
    ("Motion for Surplus Funds", "claim", True),
    ("Civil Cover Sheet - Claim Amount", "claim", False),   # must NOT match
    ("Order of Dismissal", "claim", False),

    ("Motion to Vacate Sale", "sale", True),
    ("Verified Motion to Vacate Sale", "sale", True),
    ("Defendant's Motion to Vacate Sale", "sale", True),
    ("Motion to Vacate Final Judgment and Sale", "sale", True),
    ("Motion to Vacate", "sale", False),            # bare vacate, no sale object -> NOT a sale kill
    ("Motion to Vacate Default", "sale", False),    # procedural, not the sale
    ("Motion to Cancel Sale", "sale", True),
    ("Order Granting Motion to Cancel Sale Date", "sale", True),
    ("Order Denying Motion to Vacate Sale", "sale", False),  # denied -> must NOT kill
    ("Notice of Cancellation of Hearing", "sale", False),    # hearing, not sale

    ("Suggestion of Bankruptcy", "bk_active", True),
    ("Notice of Bankruptcy", "bk_active", True),
    ("Notice of Filing Bankruptcy", "bk_active", True),
    ("Order Case Pending Bankruptcy Stay", "bk_active", True),
    ("Motion for Relief from Automatic Stay", "bk_resolved", True), # resolution signal
    ("Order Lifting Automatic Stay", "bk_resolved", True),
    ("Order Dismissing Bankruptcy", "bk_resolved", True),
]
PATS = {
    "claim": (CLAIM_FILED_PATTERNS, None),
    "sale": (SALE_ISSUE_PATTERNS, SALE_ISSUE_DENIAL_GUARD),
    "bk_active": (BANKRUPTCY_ACTIVE_PATTERNS, None),
    "bk_resolved": (BANKRUPTCY_RESOLVED_PATTERNS, None),
}
pallok = True
for title, det, should in CASES:
    pats, exc = PATS[det]
    got = bool(first(title, pats, exc))
    ok = (got == should)
    pallok &= ok
    print(f"  {'OK ' if ok else 'XX '} [{det:11}] match={got!s:5} expect={should!s:5}  {title!r}")

print("\n\n==== anti-false-kill pipeline test (synthetic dockets) ====")
def pipeline(titles):
    html = "".join(f'<div aria-label="View details for {t}"><p>{t}</p></div>' for t in titles)
    r = DocketResult(county_id="miami-dade-fl", case_number="SYNTH")
    S.parse_docket(html, r)
    S._apply_evidence_level(r)
    return r

synth = [
    # (titles, expected lead_status, expected evidence_level, note)
    (["Notice of Lis Pendens", "Final Judgment of Foreclosure", "Suggestion of Bankruptcy"],
     "pursuable_with_caution", "bankruptcy_found", "Rule 1: bankruptcy ALONE -> caution, NOT killed"),
    (["Suggestion of Bankruptcy", "Order Lifting Automatic Stay"],
     "pursuable_with_caution", "bankruptcy_found", "bk resolved -> caution"),
    (["Motion for Relief from Automatic Stay", "Final Judgment of Foreclosure"],
     "pursuable_with_caution", "bankruptcy_found", "relief-from-stay only -> caution"),
    (["Suggestion of Bankruptcy", "Motion to Vacate Sale"],
     "not_pursuable", "sale_issue_found", "Rule 2 EDGE: bk + vacate -> hard kill on SALE"),
    (["Notice of Bankruptcy", "Order Granting Motion to Cancel Sale Date"],
     "not_pursuable", "sale_issue_found", "bk + cancel-sale order -> hard kill on SALE"),
    (["Order Denying Motion to Vacate Sale", "Final Judgment of Foreclosure"],
     "pursuable", "no_claim_found", "denied vacate -> sale stands -> pursuable"),
    (["Notice of Appearance", "Final Judgment of Foreclosure", "Certificate of Sale"],
     "pursuable", "no_claim_found", "clean -> pursuable"),
]
sallok = True
for titles, exp_ls, exp_ev, note in synth:
    r = pipeline(titles)
    ok = (r.lead_status == exp_ls and r.evidence_level == exp_ev)
    sallok &= ok
    print(f"  {'OK ' if ok else 'XX '} lead={r.lead_status:22} ev={r.evidence_level:16} "
          f"expect={exp_ls}/{exp_ev} | {note}")
    if not ok:
        print(f"       reason={r.classification_reason} ks={r.kill_signals}")

print(f"\nCLASSIFICATION all-correct: {allok}")
print(f"PHRASE-BREADTH all-correct: {pallok}")
print(f"ANTI-FALSE-KILL all-correct: {sallok}")
