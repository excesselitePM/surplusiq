# Broward (CaseSearchECA) — docket portal investigation (2026-06-10)

INVESTIGATION ONLY. No scraper built. Probe = plain HTTP (curl) from a residential
IP; the headless/datacenter-IP confirmation + the docket-detail page are still
PENDING a browser probe (see "Open gap" below).

## Portal type
- **ASP.NET MVC** (Razor). Evidence: `ASP.NET_SessionId` cookie, MVC
  `__RequestVerificationToken`, route `/Web2/CaseSearchECA/Index`, `X-UA-Compatible: IE=edge`.
  NOT WebForms (no `__VIEWSTATE`/`__EVENTVALIDATION`). Results render in **Kendo UI grids**
  populated from embedded escaped-JSON arrays.

## CAPTCHA — reCAPTCHA **v2** (challenge), but ONLY on the interactive forms
- `api.js?onload=CaptchaCallback&render=explicit`; `grecaptcha.render('RecaptchaField1..4', {sitekey:'6LeomjoqAAAAANqUs56ZxerFIcoUS1qL14rTH4aF', callback:onCaptchaSolvedN})`.
- This is v2 (interactive challenge), NOT Miami's v3 invisible score. It would NOT auto-pass headless.
- The 4 widgets sit one-per-search-form, each posting to a `...CAPTCHA` endpoint:
  - `personSearchForm`   → `/CaseSearchECA/PersonSearchResultsCAPTCHA`   (RecaptchaField1)
  - `businessSearchForm` → `/CaseSearchECA/BusinessSearchResultsCAPTCHA` (RecaptchaField2)
  - `caseSearchForm`     → `/CaseSearchECA/CaseNumberSearchResultsCAPTCHA` (RecaptchaField3)  ← case-number search
  - `citationSearchForm` → `/CaseSearchECA/CitationSearchResultsCAPTCHA` (RecaptchaField4)

## KEY FINDING — a non-CAPTCHA PUBLIC GET path exists (do NOT fight the v2)
Parallel to each `...CAPTCHA` form action there is a `...PUBLIC` GET endpoint that
returns the same results WITHOUT any reCAPTCHA:

    GET /Web2/CaseSearchECA/CaseNumberSearchResultsPUBLIC?CaseNum=<CASENO_NO_DASHES>
      → 302 → /Web2/CaseSearchECA/Results?TYPE=GetCaseSearchByCase_ECA&INPUT=<session-encrypted blob>
      → 200  Kendo grid with the case row (CaseID token, CaseNumber, CourtType,
             DispositionCode, CaseStatusDesc/Date, Style = parties)

- Confirmed via plain curl (cold AND warmed session) — ZERO reCAPTCHA on this path.
- Generalizes across prefixes: CACE…, COCE… confirmed (200, case present). Case # format
  is prefix+year+seq, **no dashes** (CACE-13-021361 → `CACE13021361`).
- Sample files:
  - `brow_search_landing.html`        — the v2-gated search form page (RecaptchaField1..4)
  - `brow_caselist_CACE13021361.html` — PUBLIC case-list result, no CAPTCHA (Santander Bank vs William G Perez, Reopened Active)
  - `brow_caselist_COCE25070098.html` — PUBLIC case-list result, no CAPTCHA (Mariposa Pointe… condo)

## Navigation map (case number → docket)
1. GET landing `/Web2/CaseSearchECA/Index/?AccessLevel=ANONYMOUS` → session cookie.
2. GET `CaseNumberSearchResultsPUBLIC?CaseNum=<NODASH>` → 302 → `Results?TYPE=GetCaseSearchByCase_ECA&INPUT=<enc>` (case list).
3. Case row → `ViewDetails(CaseID)` → JS submits hidden form `#dynamicViewCaseDetail`
   (POST `Viewer=<CaseID>` to `/CaseSearchECA/CaseDetailViewer`)
   → 302 → `/CaseSearchECA/GetCaseDetail?Viewer=<token>` → case detail + DOCKET.
   - `CaseID` token is session-encrypted, e.g. `NzEwMTMzMw%3d%3d-QqWcGCfyn4o%3d`
     (base64 "7101333" + session-bound checksum; the checksum varies per session).

## Open gap — docket-detail hop NOT retrievable via bare HTTP (NOT a CAPTCHA block)
Replaying step 3 with curl (fresh same-session token, every encoding variant, correct
Referer) always 302s to `/Web2/Error?aspxerrorpath=…/GetCaseDetail` — an ASP.NET server
exception, **never a reCAPTCHA challenge**. The detail hop depends on browser/JS session
state (or an AJAX endpoint) that curl doesn't reproduce. `brow_getcasedetail_ERROR_page.html`
is the error page.
→ Retrieving the actual docket (and therefore the claim/kill VOCAB check + full-vs-paginated
  question) requires a real headless browser session OR reverse-engineering the detail AJAX
  endpoint. It does NOT require solving a CAPTCHA.

## Reuse estimate (Miami-Dade classifier core)
- Classification logic (claim/sale-issue/bankruptcy detection, evidence-level precedence,
  flag-not-kill rule, kill-on-sale-issue) is portal-agnostic → **high reuse** once fed a
  list of docket-entry texts.
- Docket-entry EXTRACTION is Miami-specific (keys on `aria-label="View details for …"`
  card markup). Broward renders a Kendo grid/table → needs a **Broward-specific extractor**.
- Navigation layer is **entirely new** (HTTP session + PUBLIC path + detail hop), and
  simpler than Miami's Playwright SPA IF the detail hop is solved.

## Vocab check — PENDING (not asserted; anti-fabrication)
Could not confirm whether the SOP claim terms ("Notice of Appearance", "Certificate of
Disbursement", "Motion for Surplus Funds", "Homeowner's Claim for Surplus Funds") appear
verbatim, because the docket-entry page was not retrieved. To be done in the browser probe.
