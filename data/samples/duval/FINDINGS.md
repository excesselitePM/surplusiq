# Duval (Tyler CORE CMS) — Investigation Findings

Investigation only. NO production scraper built; NOT registered; cron untouched.
Evidence: GitHub Actions `Duval Investigate` probe (`scripts/duval_investigate.py`),
runs 27976184152 / 27976440875 / 27976675772 / 27977194992 (datacenter IP).
Samples in `ci/` are real captured dockets (full visible text + one detail HTML).

---

## PHASE 1 — PORTAL CHARACTERIZATION

### Portal type
**ASP.NET WebForms + Tyler "CORE" (Clerk Online Resource ePortal).**
- `has_viewstate: true`, `has_aspnet_form: true`, jQuery 1.12.4, many `ScriptResource.axd`.
- NOT a SPA (no React/Angular root). NOT a static page.
- **JSON backend = ASMX web service** `DuvalClerk.Web.Core.CoreWebSvc` at
  `/internal/CoreWebSvc.asmx`. Observed methods: `PublicLogin`, `GetNewSearchTab`,
  `GetCaseByUcn`. The UI is an MDI tab shell; case data is fetched by AJAX.
- Portal SSL **certificate is expired** → must use `ignore_https_errors=True`.

### CAPTCHA — passes headless from CI (like Miami v3, NOT a Broward-style wall)
- reCAPTCHA present: `recaptcha/api.js?onload=onloadCallback&render=explicit` →
  this is **reCAPTCHA v2 (explicit render)**, wired only to the **registered-user
  `LoginDialog`** (`attemptLogin()` / "Login to CORE").
- **Anonymous "Public Access" needs NO captcha solve.** On load the page auto-calls
  `CoreWebSvc.asmx/PublicLogin` and the footer shows **"Login Status: Public Access"**
  — confirmed headless from the Actions datacenter IP. No challenge, no score wall.
- There IS a "CORE - Required Captcha Challenge" dialog in the DOM, but it is only
  invoked on the registered-login path; the Public Access path never triggers it.
- One timing caveat (handled): a fresh browser context must WAIT for `PublicLogin`
  to settle (`#c_AccessTypeLabel` == "Public Access") BEFORE opening search, or the
  login/captcha dialog appears instead of the search form.

### Navigation path (proven, stable headless)
1. GET `https://core.duvalclerk.com/CoreCms.aspx?mode=PublicAccess` → auto `PublicLogin`.
2. Left nav **Court Records → Case Search** = `<td onclick="openCmsPage();">`
   (id `c_CaseSearchItem`) → `GetNewSearchTab` renders the search form (single frame,
   **no iframe**).
3. **Paste-full path**: type the full uniform case number into `c_UcnEntryBox_<GUID>`
   (GUID is per-tab; match by id prefix) → "Open Case" button
   `onclick="getCaseTabByUcnBoxId('c_UcnEntryBox_<GUID>')"` → `GetCaseByUcn` →
   the full case-detail tab renders inline. (Alt: criteria search via
   `c_UcnYearTextBox`/`c_CourtTypeDropDownList`/Begin Search → `getCaseSearch`.)
4. No per-case deep-link URL (session/tab-bound) — store the stable
   `clerk_search_url` for the dashboard verify flow, same as the other counties.

### Full docket capture — YES, one page, no pagination
- Case 16-2025-CA-005932 returned its **entire 74-entry docket on one page**.
- Built-in completeness check: docket "Line" numbers are **sequential 1..N with no
  gaps and no pager** → `max(Line) == entry count` verifies a complete capture.
- The case-detail tab also carries, fully server-rendered:
  **Case header** (Dept, Division `FC-F`=foreclosure, Status, File Date, Judge),
  **Parties** (Name / Party Type / Address), **Attorneys** (Attorney / Address /
  **For Parties**), **Fees**, **Court Events**, **Dockets**.

---

## PHASE 2 — VOCABULARY GROUND-TRUTH (7 real dockets)

Sampled the oldest available sold mortgage (CA) cases (sold 26–74 days ago, biased
to large 3rd-party surplus). Tax-deed (`*TD`) numbers are a SEPARATE portal
(`taxdeed.duvalclerk.com`) — excluded.

### Which column holds the signal → **Description**
The "Dockets" table columns are: `Line / Document | Count | Effective/Entered |
**Description** | Pages | Image`. ALL verbatim filing text (kill, positive, party)
lives in **Description**. (Contrast Broward, where it was "Additional Text." There is
NO separate additional-text column in Duval — scan Description.)

### Surplus AMOUNT is stated DIRECTLY in the docket (major finding)
`CERTIFICATE OF FORECLOSURE DISBURSEMENT $<paid> TO: <plaintiff> ... BALANCE: $<X>`
— `BALANCE` is the **surplus held in the registry**. Real examples:
- 010844: disbursed $227,190.85 to Nationstar → **BALANCE: $29,609.15**
- 005932: disbursed $55,596.27 to U.S. Bank → **BALANCE: $42,403.73**
- 006061: disbursed $73,741.91 to Habitat for Humanity → BALANCE: (present)
Also `CIR/FORECLOSURE-Registry $<sale proceeds>` in the Fees section. So Duval can
yield a docket-exact surplus, not just opening-bid math — stronger than the current
FL `fl_opening_bid` path. (NOTE: a `MOTION ... OBJECTION TO THE CERTIFICATE OF
DISBURSEMENTS` for additional plaintiff advances can later reduce the balance — read
the latest disbursement entry, and treat a pending objection as caution.)

### Positive / neutral signals that MUST NOT flag
- `VALUE OF REAL PROPERTY OR MORTGAGE FORECLOSURE CLAIM` — standard cover-sheet form
  filed at case OPEN. Contains the word **"CLAIM"** but is NOT a surplus claim. A bare
  `claim` keyword would false-fire on **every** case. (Duval's version of the trap.)
- `CERTIFICATE OF SALE`, `CERTIFICATE OF TITLE`, `FINAL JUDGMENT OF FORECLOSURE` —
  positive; sale completed.
- `ANSWER CONDITIONAL DISCLAIMER OF THE UNITED STATES OF AMERICA` / `... USA HOUSING
  AND URBAN DEVELOPMENT` — govt junior lienholder, benign/common.

### Sale cancel / vacate churn is NOISE pre-sale (Broward lesson reconfirmed)
Both 005932 and 004483 had vacate/cancel motions, THEN sold:
- 005932: cancelled+rescheduled 3× (`MOTION TO CANCEL FORECLOSURE SALE AND
  RESCHEDULE`, `ORDER ON MOTION TO CANCEL ... RESCHEDULE`, `NOTICE OF CANCELLATION OF
  SALE PER ORDER`) → then `CERTIFICATE OF SALE` + `CERTIFICATE OF TITLE`. $42K surplus.
- 004483: `MOTION TO VACATE FORECLOSURE / POSTPONE FORECLOSURE SALE`, `ORDER
  CANCELLING FORECLOSURE SALE` → then `CERTIFICATE OF SALE ISSUED TO MARSH VIEW
  INVESTMENTS`. Sold anyway.
→ A naive vacate/cancel matcher false-kills sold-with-surplus cases. Only a vacate
AFTER the Certificate of Title is a real kill (date-ordered logic, deferred — same
as Broward). We only scrape auction-confirmed SOLD cases, so the sale completed.

### Bankruptcy = FLAG not kill (Eric Rule 1), Duval vocabulary
`SUGGESTION OF BANKRUPTCY ISSUED FOR: <name> BK# <no>` (010844, two of them) — yet
the case SOLD with a $29,609.15 surplus. Verbatim term: **"SUGGESTION OF BANKRUPTCY"**
(matches Miami's `BANKRUPTCY_ACTIVE_PATTERNS`).

### NOA "who appeared" — Duval SOLVES the Broward 3-stage problem with data
Duval's NOA text embeds the represented party inline:
`NOTICE OF APPEARANCE OF COUNSEL <attorney> FOR <party>`. Real examples:
- `... COUNSEL MICHAEL MCCABE FOR SOUTHERN GROVE CONDOMINIUM ASSOCIATION, INC.` (HOA)
- `... COUNSEL KATELYN HARDWICK FOR WINTER KATTERHENRY`
- `... COUNSEL MICHELLE FUSILLO FOR TARPLEY MCCOLL`
Plus the **Parties** and **Attorneys (For Parties)** sections cross-reference every
attorney→party. So a recovery-firm NOA (names a non-party / surplus vocabulary) is
distinguishable from benign defense counsel by matching "FOR <party>" against the
case parties — no fragile 3-stage heuristic needed.

### Eric SOP term presence
- Present verbatim/variant: Certificate of Sale; Final Judgment; **Suggestion of
  Bankruptcy**; **Certificate of (Foreclosure) Disbursement**; Notice of Appearance;
  Motion to Cancel/Vacate Sale.
- **NOT FOUND in any sampled case**: `Motion for Surplus`, `Claim to Surplus`,
  `Motion to Intervene`, recovery-firm NOA, surplus disbursement to a third party.

### Surplus-CLAIM (kill) vocabulary — GROUND-TRUTHED (option a, party-name search)

The 7 recently-sold cases had no claims (Duval claims lag the sale/disbursement by
months). Sourced older CLAIMED cases via the portal's **party-name search**
(`scripts/duval_party_search.py`, run 27979917867): Last Name = recovery-firm term
→ `getCaseSearch` → results grid. 8 searches + 8 opened dockets (well under budget).
Real claim vocabulary below; all in the Dockets **Description** column. Samples in
`party/*_docket.txt`.

**Recovery-firm party signature (structured — the cleanest kill signal):**
The claimant is added as a case **party with type `3rd Party` / `THIRD PARTY
DEFENDANT`** (the `(3)` / `(T)` code in the results grid). Real:
- `Surplus Refund Corp.  3rd Party` (2016-CA-002623, 2016-CA-006997)
- `Surplus Return Group  THIRD PARTY DEFENDANT` (2017-CA-000908)

**Surplus-claim motions/orders (Description column, verbatim):**
- `MOTION TO AUTHORIZE DISBURSEMENT OF SURPLUS FUNDS (SURPLUS REFUND CORPORATION)`
- `MOTION FOR DISBURSEMENT OF REGISTRY FUNDS TO AUTHORIZE DISBURSEMENT OF SURPLUS FUNDS FILED BY SURPLUS REFUND CORPORATION`
- `MOTION TO DISBURSE SURPLUS FUNDS` / `MOTION TO DIRECT CLERK TO DISBURSE SURPLUS FUNDS TO PLTFF`
- `PETITION FOR DISBURSEMENT OF SURPLUS FUNDS (SURPLUS TRUSTEE'S)`
- `NOTICE OF APPOINTMENT OF SURPLUS TRUSTEE (SURPLUS RETURN GROUP, LLC)`
- `REQUEST (BANK OF AMERICA NA) AND NOTICE OF INTENT TO CLAIM EXCESS PROCEEDS` (junior lienholder)
- `ORDER AUTHORIZING DISBURSEMENT OF SURPLUS FUNDS` / `... SURPLUS PROCEEDS`
- `ORDER GRANTING SURPLUS TRUSTEES PETITION FOR DISBURSEMENT OF SURPLUS FUNDS`
→ Match rule: **"surplus" anchor + a disburse/claim/petition/intent verb** (same
shape as Broward), so the `VALUE OF ... CLAIM` cover sheet and routine plaintiff
disbursements never false-fire.

**Surplus fee-codes (Fees section — structured, unambiguous kill marker):**
`SURPLUS-DISB PROCEEDS-EA`, `SURPLUS-APPOINT TRUSTEE`, `SURPLUS-NOTIFY TRUSTEE APPT`.
These appear ONLY when a surplus disbursement/trustee process ran; the 7 clean cases
had none. A clean structured signal independent of Description text.

**Recovery-firm NOA — structure confirmed (solves the Broward 3-stage problem):**
`NOTICE OF APPEARANCE OF COUNSEL <attorney> FOR <party>` holds for BOTH benign and
recovery NOAs — the represented party is named inline:
- recovery: `NOTICE OF APPEARANCE OF COUNSEL STEVEN IMPARATO FOR SURPLUS REFUND CORP.`
            `NOTICE OF APPEARANCE OF COUNSEL JUSTIN MOOREFIELD FOR SURPLUS RETURN GROUP`
- benign:   `... FOR SHAIB RIOS FOR BANK OF AMERICA, N.A.` / `... FOR <homeowner/HOA>`
→ Detect a recovery-firm NOA by matching the `FOR <party>` against surplus/recovery
vocabulary or the 3rd-party firm list — no fragile heuristic.

**Disbursement BALANCE = surplus amount (confirmed across cases):**
Two formats: recent = one line `CERTIFICATE OF FORECLOSURE DISBURSEMENT $X TO:
<plaintiff> BALANCE: $Y`; older = a SEQUENCE of `CERTIFICATE OF DISBURSEMENTS DISBURSE
$X TO: <payee> (BAL $Y)` lines, each reducing the balance, first to the plaintiff
(payoff) and last to claimant/owner ending `(BAL. $0.00)`. **Surplus = balance after
the first (plaintiff) disbursement.** Caveat: `MOTION FOR ADDITIONAL ADVANCES FROM THE
REGISTRY OF THE COURT` (plaintiff clawback) can reduce it — treat a pending one as
caution.

### Cross-county firm check (point 4) — Duval needs its OWN known-firm list
Jacksonville is dominated by a LOCAL firm set, not Broward's. Counts (party search):
- **SURPLUS REFUND CORP / SURPLUS REFUND CORPORATION** — the heavyweight (~15+ FC
  cases; counsel Kenner & Imparato PLLC / Steven Imparato, Boca Raton).
- **SURPLUS RETURN GROUP, LLC** — acts as court-appointed "surplus trustee" (counsel
  Justin Moorefield). Also: Surplus Recovery LLC, Surplus Funds Recovery SE, Surplus
  Funds USA LLC, National Equity Recovery.
- Broward firms mostly ABSENT; only **GET LIQUID FUNDING** reaches Duval (rarely —
  e.g. 3rd party alongside National Equity Recovery in 2023-CC-001783). PRIORITY
  SURPLUS / NEW BEGINNINGS / PRESTIGE / CAPITAL CRAFTER / EVO RECOVERY: 0 Duval hits.

### Gap status: CLOSED
Real Duval claim vocabulary obtained. Ready to build a production scraper porting the
Miami-Dade evidence model with Duval-specific (a) Description-column docket extractor,
(b) surplus-claim patterns above, (c) 3rd-party/fee-code/NOA-party kill signals, (d)
disbursement-BALANCE surplus extraction, (e) a Duval-local known-firm list.

---

## REUSE ESTIMATE

Closest template: **`core/dockets/miami_dade.py`** (FL evidence model).
- **Classifier core (~70–80% portable):** claim / sale-issue / bankruptcy precedence,
  `_apply_evidence_level`, the `classify()` no-op override, bankruptcy-flag-not-kill,
  sale-issue denial-guard, the three-tier money model — all transfer directly.
- **Docket extractor — Duval-specific but SIMPLE:** parse the server-rendered
  "Dockets" table Description column (no Kendo grid, no aria-label cards, no
  pagination). Easier than both Broward and Miami.
- **Navigation — Duval-specific, proven:** WebForms + ASMX, landing → (wait Public
  Access) → `openCmsPage()` → `c_UcnEntryBox` → `getCaseTabByUcnBoxId` →
  `GetCaseByUcn`. Stable headless from CI.
- **Two Duval advantages that REDUCE work vs other counties:** (a) surplus amount is
  in the docket (disbursement BALANCE) — no PDF debt extraction (OH) and stronger than
  opening-bid math; (b) NOA names the represented party inline — no Broward 3-stage
  NOA classifier needed.

Net: a Duval scraper is **less** work than Broward once the claim vocabulary is
ground-truthed — the navigation is proven and the hard money/NOA problems are eased
by Duval's richer docket. The one blocker is the missing claim-vocabulary sample.
