# Orange (MyEClerk) — Investigation Findings

Investigation only. NO scraper built; NOT registered; cron untouched.
Evidence: GitHub Actions `Orange Investigate` probe (`scripts/orange_investigate.py`,
since removed), runs 28039452160 / 28039665046 / 28039816353 (datacenter IP).
Screenshots in `ci/` (B_search.png = search form; C_captcha_*.png = the wall).

## VERDICT: BLOCKED headless — reCAPTCHA v2 image challenge on the search

The Orange case search cannot be driven headless from the Actions datacenter IP.
This blocks BOTH docket retrieval (Phase 1) AND the party-name-search vocab method
(Phase 2) — they go through the same gated form. Unlike Duval (public-login
bypass, no captcha) and Miami-Dade (v3 score passes headless), no headless path
was found for Orange. **Recommendation: do NOT build an Orange docket scraper on
the current public path.** Options below.

## PHASE 1 — Portal characterization

### Portal type — Tyler "MyEClerk" (Odyssey Portal), ASP.NET **MVC** — NOT Duval
- `myeclerk.myorangeclerk.com`, "Build V. 4.1.0.3". MVC routes (`/Cases/Search`,
  `/Home/Index`, `/Account/Login`), jQuery/modernizr/bootstrap/customjs bundles,
  UserWay accessibility widget. NO `__VIEWSTATE`.
- **Distinct product from Duval's `CoreCms.aspx`** (WebForms + ASMX). Despite both
  being "Tyler," they share almost no navigation surface — Orange is NOT a Duval
  clone. The only reuse is the county-agnostic classifier core (see Reuse).

### CAPTCHA — TWO reCAPTCHAs; the v2 one walls the search
- Landing: reCAPTCHA **v3** (score) — `api.js?render=6LcHabksAAAAAK0dubEteqz3Pr-CcbveTZe9hQtG`.
- Search form (`/Cases/Search`): a reCAPTCHA **v2 "I'm not a robot" checkbox**
  (`g-recaptcha-response` token field) that MUST be solved — submitting without a
  token returns a server error page ("Error Code: 48252494", run 28039452160).
- **Decisive test (run 28039816353):** clicking the v2 checkbox headless from the
  datacenter IP → an **image challenge** pops ("Select all images with crosswalks",
  3×3 grid; see `ci/C_captcha_2024CC022302O.png`); no token issued
  (`v2_challenged: true, v2_token_len: 0`) for BOTH probed cases. The landing v3
  score does NOT auto-pass the v2 checkbox from this IP.
- CAPTCHA-solving is off the table (project rule) → the search is walled.

### Navigation / bypass search
- Search form fields (proven, `ci/B_search.png`): Case Type (dropdown), First /
  Middle / Last Name, Business Name, Case Number, Citation Number, Date From/To,
  + the v2 checkbox + Search button.
- **No non-captcha / API bypass found.** The search HTML exposes only
  `/Cases/Search`; no `/api/`, `.asmx`, `.svc`, or direct `CaseDetail?...`
  endpoint. (Contrast Broward, which had a non-CAPTCHA `…PUBLIC` endpoint.)
- Full-docket capture: NOT reachable (never got past the search gate).

## PHASE 2 — Claim vocabulary
NOT obtainable. The party-name search (the Duval method for sourcing older claimed
cases) uses the same captcha-gated `/Cases/Search`, so it is blocked too. Orange
claim vocabulary, structured signals, surplus-in-docket, NOA party-naming, and the
Orange-local recovery-firm set could NOT be ground-truthed.

## Reuse estimate vs Duval
LOW for navigation — Orange (MVC Odyssey Portal, v2-image-challenge) shares no
access path with Duval (CoreCms.aspx, public-login). The county-agnostic
classifier core (claim/sale-issue/bankruptcy precedence, evidence_level taxonomy,
owner extraction, three-tier model) would still port IF dockets were retrievable —
but they are not, headless. So Orange is NOT a near-clone of Duval.

## Options (for review — NOT actioned)
1. **Leave Orange auction+PR only** (current state): no docket layer; leads stay
   `apparent_surplus`/PR-enriched. Honest, no false data.
2. **Residential/proxy IP** for the Orange step so the v2 checkbox auto-passes
   (no image challenge from a residential IP). Needs infra; unverified.
3. **A CAPTCHA-solving service** — explicitly OUT (project rule).
4. **Tyler bulk/API access** (if the Orange Clerk offers an authenticated data
   feed) — a procurement/credential path, not scraping.
