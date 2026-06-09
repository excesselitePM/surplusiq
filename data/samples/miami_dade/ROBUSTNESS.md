# Miami-Dade Detector — Robustness Review

**Date:** 2026-06-09
**Concern:** the one failure mode that ships a dead lead as good — a FALSE
`no_claim_found / pursuable` on a lead that actually has a claim or kill signal
(and its inverse, a FALSE kill on a good lead from a stale signal).

Tests: `_test_robust.py` (capture), `_test_hardened.py` (logic + phrases +
anti-false-kill). Saved dockets: `robust_<case>.html`.

## 1. Selector robustness — SOLID (no truncation, no missed rows)

The parser keys on `aria-label="View details for <TITLE>"`. Verified the full
docket is captured, not a first-screen subset:

| case | Hearings + Parties + Dockets | aria-label count | match? |
|---|---|---|---|
| 2025-010668-CA-01 | 2 + 14 + 109 | 125 | ✓ |
| 2024-020538-CA-01 | 5 + 4 + 70 | 79 | ✓ |
| 2025-000672-CA-01 | 4 + 5 + 117 | 126 | ✓ |
| 2019-001371-CA-01 | 6 + 5 + 144 | 155 | ✓ |
| 2017-021344-CA-01 | 12 + 5 + 122 | 139 | ✓ |

- aria-label count **exactly equals** the sum of every section's "N results
  returned" — every docket entry is present and carries the aria-label.
- `aria-label` count == fw-bold `<p>` count for every case → no entry uses a
  different markup that the selector would miss.
- The docket is **NOT lazy-loaded/paginated**: it renders fully in the initial
  `page.content()`. Expanding the collapsed accordions and scrolling to the
  bottom (in `_test_robust.py`) produced the **same** counts as the parser's
  non-expanded fetch. Collapsed sections are `height:0` but fully in the DOM.

## 2. Phrase-match breadth — widened to regex (19/19 variants)

Detection now keys on docket **titles** with case-insensitive regex, not a
flat full-text blob (avoids chrome/party-name false positives). Verified:

- Claim: "Owner's Claim for Surplus", "Owners Claim for Mortgage Foreclosure
  Surplus", "Homeowner's Claim for Surplus", "Motion for Surplus Funds" all
  match; "Civil Cover Sheet - Claim Amount" and "Order of Dismissal" correctly
  do NOT (every claim pattern requires "surplus" near "claim"/"disburs").
- Sale issue: "Motion to Vacate", "Verified Motion to Vacate Sale",
  "Defendant's Motion to Vacate", "Motion to Cancel Sale", "Order Granting
  Motion to Cancel Sale Date" match; "Order Denying Motion to Vacate Sale" and
  "Notice of Cancellation of Hearing" correctly do NOT.
- Bankruptcy: "Suggestion of Bankruptcy", "Notice of Bankruptcy", "Notice of
  Filing Bankruptcy", "Order Case Pending Bankruptcy Stay" match active.

## 3. The 3 bankruptcy kills — confirmed, plus a false-kill class fixed

Actual triggering docket titles:

| case | bankruptcy titles | resolution signal? | verdict |
|---|---|---|---|
| 2025-000672-CA-01 | "Notice of Bankruptcy", "Suggestion of Bankruptcy" | none | **genuine kill** (also has "Order Granting Motion to Cancel Sale Date") |
| 2019-001371-CA-01 | "Suggestion of Bankruptcy" ×3 | none | **genuine kill** (also "Motion to Cancel Sale") |
| 2017-021344-CA-01 (baseline) | "Order Case Pending Bankruptcy Stay" ×2, "Suggestion of Bankruptcy" ×4 | **yes** — "relief from automatic stay" + "reopen" in docket; sold 2026 | **was a FALSE kill** → now `pursuable_with_caution` |

The two real today-leads are genuine. The baseline exposed the exact failure
mode flagged in review: a bare `"bankruptcy"` substring would kill a case whose
stay was later lifted. Fix:

- **Active vs resolved split.** Kill only on an ACTIVE bankruptcy
  (suggestion / notice / pending stay / automatic stay / chapter 7·11·13) with
  **no** resolution signal. Active + resolution (relief-from-stay, stay
  lifted, bankruptcy dismissed/closed/discharged, reopened) →
  `pursuable_with_caution`, never an auto-kill. The bare-"bankruptcy" kill is
  gone.
- **Denial guard on sale issues.** A title with deny/denied/withdrawn/moot/
  stricken is not counted — "Order Denying Motion to Vacate Sale" leaves the
  lead pursuable.

Anti-false-kill pipeline test (synthetic dockets) confirms: relief-from-stay
only → caution; active bk + stay lifted → caution; active bk alone → killed;
denied vacate → pursuable; clean → pursuable.

## Final classification of the verified cases

| case | evidence_level | lead_status | driving title |
|---|---|---|---|
| 2024-020538-CA-01 | claim_filed | not_pursuable | "Owners Claim for Mortgage Foreclosure Surplus" |
| 2025-000672-CA-01 | bankruptcy_found | not_pursuable | "Notice of Bankruptcy" |
| 2019-001371-CA-01 | bankruptcy_found | not_pursuable | "Suggestion of Bankruptcy" |
| 2017-021344-CA-01 | pursuable_with_caution | pursuable_with_caution | bk present + relief-from-stay |
| 2025-010668 / 2025-012741 / 2019-009163 / 2025-095651 | no_claim_found | pursuable | clean |

## Known limitations (flagged, not yet addressed)

- **No chronological ordering.** Detection is a flat title scan, not
  "signal after the most recent Certificate of Sale". A claim later withdrawn
  still marks not_pursuable — acceptable under Eric's "a filed claim = not
  pursuable" rule, and conservative (won't ship a dead lead as good). A
  granted-then-renoticed sale that ultimately held would mark not_pursuable;
  these leads did sell, so a sale-issue flag means real docket complexity worth
  a human look. If false-kills on this become an issue, add date-anchored
  ordering relative to the last sale/judgment.
- **Claim requires "surplus" near "claim".** A bare "Owner's Claim" with no
  surplus wording won't match (intentional — avoids exemption-claim false
  positives). Real surplus claims always name the surplus.
