# Broward docket — CI headless retrieval + vocabulary check (2026-06-10)

Source: GitHub Actions run 27295061373 (datacenter IP, headless Chromium).
Probe: scripts/broward_investigate.py. Artifacts: data/samples/broward/ci/.

## Headless retrieval from CI — WORKS
- PUBLIC path returns case data from the datacenter IP with ZERO reCAPTCHA
  (url_has_CAPTCHA / recaptcha_iframe / challenge_text all False), both cases.
- Detail hop now reaches `…/GetCaseDetail?Viewer=<blob>` (200, no CAPTCHA, no
  error). The trigger is a `<button class="bc-casedetail-viewer"
  onclick="ViewDetails(`<~152-char Viewer blob>`)">` in the results grid — click
  it (faithful user action). The short `CaseID` token is NOT the detail token.

## Docket structure — plain HTML tables, FULL on one page (not paginated)
Detail page = static HTML tables (no Kendo grid, no pager). Docket table headers:
`Date | Description | Additional Text | View / Pages`. Per-section `Total: N`
badges cross-check the row counts:
- CACE-13-021361: docket table = 231 entries, badge `Total: 231`  → full.
- CACE-24-010415: docket table = 100 entries, badge `Total: 100`  → full.
The non-round 231 proves the page renders ALL events (no 100/200 cap). The
`Description` column carries the verbatim filing title; `Additional Text` carries
party/role + free-text detail.

## Vocabulary vs Eric's Broward spec (actual entry text)
POSITIVE/neutral terms appear VERBATIM in `Description` and were correctly NOT
flagged: `Certificate of Sale`, `Final Judgment`, `Notice of Sale`, `Lis Pendens`
(also `Certificate of Title`). Filing titles are clean/verbatim, so the KILL
terms would likewise match the `Description` column verbatim when present.

KILL terms OBSERVED in these two cases: only `Notice of Appearance`. The
surplus-claim kill terms (Motion for/to Disburse Surplus, Claim/Petition for
Surplus, Certificate of Disbursement, Order Disbursing Surplus, Assignment of
Surplus Rights, Stipulation for Surplus) did NOT appear — expected, because both
cases are unclaimed-surplus leads. NOT YET CONFIRMED VERBATIM: need a case that
actually has a surplus claim filed to validate those exact strings.

## Notice of Appearance — DOES the entry name who appeared?  YES.
`Description` is generic ("Notice of Appearance"); the appearing party is in
`Additional Text`, in two forms:
- Structured: `Party: Plaintiff <name>` / `Party: Defendant <name>`  → benign.
- Free text: firm/company description  → needs judgment.

Real entries (CACE-13-021361):
- 06/02/2026  ADDL: `Third Party Highest Bidder, NASINNYA LLC`     ← THIRD PARTY (surplus-circling signal)
- 02/06/2020  ADDL: `GHIDOTTI /BERGER LLP Attorneys for the Plaintiffs Assignee …`  ← plaintiff counsel (benign)
- 02/26/2016  ADDL: `OF CO-COUNSEL Party: Plaintiff Santander Bank , N.A.`           ← plaintiff co-counsel (benign)
- 10/09/2013  ADDL: `Party: Defendant Perez, William G`                              ← defendant/homeowner (benign)
CACE-24-010415:
- 02/26/2025  ADDL: `U.S. BANK TRUST NATIONAL ASSOCIATION … OWNER TRUSTEE FOR RCF 2 ACQUISITION TRUST` ← plaintiff/lender (benign)
- 11/19/2024  ADDL: `Party: Defendant Osorio, Harold Defendant Osorio, Guillermo`    ← defendants (benign)

### Detection implication (Eric's hardest item)
The appearing party IS available, so the surplus-company-vs-normal-party split is
DOABLE — but it is NOT a single keyword on `Description`. It needs:
  1. Parse `Additional Text` for `Party: Plaintiff|Defendant` → benign (known case party / their counsel).
  2. Otherwise classify the named firm: a third party / LLC / surplus-recovery or
     funding company → KILL/flag. NOTE the ambiguity: "Third Party Highest Bidder,
     NASINNYA LLC" is a third party but is the auction WINNER, not necessarily a
     surplus-funding firm — Eric's spec kills specifically on surplus/funding
     companies. Distinguishing a surplus-recovery firm from other third parties
     (highest bidder, lienholder) needs a known-company list or a heuristic, not a
     plain string match. This is the one genuinely hard classifier in the build.
