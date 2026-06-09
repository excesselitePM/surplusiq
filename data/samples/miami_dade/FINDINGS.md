# Miami-Dade Local Case Search — Block Re-Characterization

**Date:** 2026-06-09
**Test case:** 2017-021344-CA-01 (year=2017, seq=021344, code=CA, location=01)
**Question:** Is Miami-Dade Local Case Search actually blocking automated docket access, and if so by what mechanism?

## ANSWER: No hard block. Automated docket access works — headed AND headless.

The prior "reCAPTCHA-v3-blocked / out of scope" conclusion was a **misdiagnosis**.
The real obstacle was **stale selectors against a rebuilt SPA**, not the captcha.
Eric's frictionless hand-search was consistent with reality the whole time.

## Evidence

### 1. The portal was rebuilt as a React/Vite SPA
- Old assumption: classic ASP.NET `LocalCaseSearch.aspx` with `name*='year'` / `name*='caseNum'` / `name*='caseType'` inputs and a "Local Case Search" `<a>` link.
- Reality: `id="root"` React app, ES-module bundle `index-DUEPVCK8.js`. Page hydrates fully in headless (full header/nav/footer present — rendering was never the problem).
- Nav is **not** an `<a>`: it is `<span class="cursorPointer subitem-color" role="button">Local Case</span>` (text is "Local Case", not "Local Case Search"). The existing scraper's `text=/local case search/i` click never matched, so it never reached the form and reported a false "block."

### 2. Real form field names (search-form view)
| purpose | selector | type | notes |
|---|---|---|---|
| year | `#caseYear` (`select[name='caseYear']`) | select | options 2026..earlier |
| sequence number | `#caseSeq` (`input[name='caseSeq']`) | text | the 6-digit number, e.g. `021344` |
| case code | `#caseCode` (`select[name='caseCode']`) | select | value `CA` = "CA - Circuit Civil" |
| location/seq suffix | `#caseLocation` (`select[name='caseLocation']`) | select | only option is `01` (the auto `-01`) |
| submit | `button[type=submit]` text "SEARCH" | button | |

### 3. reCAPTCHA is present but is v3 invisible (score), and it PASSES
- Loads only on the **search-form view**, not the landing page.
- `https://www.google.com/recaptcha/api.js?render=6Le7np8qAAAAAAEMezDvhuXyKV4EA6BWZTvdK_E6` + anchor `size=invisible`. Site key matches the one in the prior investigation note.
- No challenge iframe, no bframe — it is score-based v3, not a v2 puzzle.
- The landing-page banner ("Avoid reCaptcha by Registering / Logging In", "Registered Access: Avoid captcha and search limits") is the ONLY place the word appears at load; it advertises that login removes search *limits*. It is not a wall.
- A plain `chromium.launch()` (default args, no `--disable-web-security`/`--no-sandbox`) with a realistic desktop UA scored high enough to pass. **No playwright-stealth, no typing/mouse choreography required.**

### 4. The search returns the full docket inline
After a correctly-filled submit the URL becomes `/ocs/searchResults?qs=<token>` and the page renders the **Case Information / Print Case Info** view containing the docket directly:
- Correct case: `WILMINGTON TRUST COMPANY, vs MANUEL ANGEL DURAN et al`, Filing Date 08/30/2017, State Case 132017CA021344000001, Status CLOSED, Type "RPMF -Homestead".
- Inline keyword counts in result text: Docket=125, Motion=56, Final Judgment=9, Bankruptcy=25, Dismiss=11, Parties=5.
- One search = whole docket. No separate Dockets/Parties sub-page navigation strictly required (though tabs exist).

### Headed vs headless
| mode | reached results? | body_len | case echo | docket mentions | blocked text |
|---|---|---|---|---|---|
| headed | yes (`searchResults?qs=`) | 24,829 | yes | yes | no |
| headless | yes (`searchResults?qs=`) | 24,829 | yes | yes | no |

Identical outcome. Empty/pre-search body was ~3.0–3.3 KB, so the 24.8 KB body is a clear positive.

## Artifacts saved (this directory)
- `01-landing.{html,png}` — SPA landing, no captcha
- `02-search-form.{html,png}` — form view, v3 badge + api.js present
- `03-form-filled.{html,png}` — filled caseYear/caseSeq/caseCode/caseLocation
- `04-results.{html,png}` — full Case Information / docket page
- `_report.json` — per-step machine log (recaptcha findings, form fields, signals)
- `_probe.py` — the investigation probe (throwaway; not wired into the pipeline)

## Recommended next step (for the build phase — not done here)
Rewrite `core/dockets/miami_dade.py` navigation/selectors for the new SPA:
1. click `span[role='button']:has-text('Local Case')`
2. fill `#caseYear`, `#caseSeq`, `#caseCode` (value=`CA`), `#caseLocation` (value=`01`)
3. click SEARCH, wait for `**/searchResults?qs=*`
4. parse the Case Information page text in place (docket events, kill signals, claim/surplus motions, Final Judgment amount) — the data Eric requires is all on this one page.

No captcha-solving pipeline is needed. Keep the default Playwright launch + realistic UA; do not add `--disable-web-security`/`--no-sandbox`.
