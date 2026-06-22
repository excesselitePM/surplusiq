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

### ⚠️ GAP — surplus-CLAIM (kill) vocabulary NOT ground-truthed
**None of the 7 sampled cases (sold 26–74 days ago) has any surplus-claim activity.**
The auction data available does not reach far enough back to sample cases old enough
to have accrued claims (Duval claims evidently lag the sale/disbursement by more than
~90 days; available Duval auction files older than April 2026 are empty). So the
kill/claim vocabulary that is the core of Phase 2 could **not** be confirmed on real
Duval text. Per the project rule ("don't code terms that aren't in real dockets"),
the production claim-detector must NOT be shipped on assumption. Options to close the
gap before/just-after build:
  1. Sample Duval cases sold ~6–18 months ago (need an older case list — e.g. pull a
     month of late-2025 sold foreclosures and re-run the probe).
  2. Build the claim detector by porting the Miami/Broward patterns but mark it
     UNVALIDATED, and validate against the first real Duval claims as they appear.

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
