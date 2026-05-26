CLAUDE.md — SurplusIQ Project Memory & Build Rules
Auto-loaded every session. Hard constraints. Overrides convenience.
PROJECT

SurplusIQ: daily lead-intelligence system finding real-estate foreclosure SURPLUS opportunities across 10 counties.
Builder: Quentin Flores (Jarvis LLC). Client: Eric Richardson (Excess Elite LLC).
Contract: $10K flat, paid, signed Apr 11 2026. Past deadline — finishing fast is the priority.
Repo: github.com/xcerebroai/surplusiq. Live dashboard: https://xcerebroai.github.io/surplusiq/
10 counties — FL: miami-dade, broward, duval, lee, orange. OH: cuyahoga, franklin, montgomery, summit, hamilton.

WORKING STYLE

Direct execution. No session timestamps, no break/stopping-point suggestions, no padding.
Quentin is not a professional developer — give exact, copy-paste-able commands. Hard technical pushback when warranted is welcome.
When delivering instructions or content to paste elsewhere, deliver as ONE single block, never split across multiple blocks.

TESTING — GITHUB-FIRST, ALWAYS

NEVER run scrapers locally. GitHub Actions is the only environment that proves anything.
Loop: edit → commit → push → trigger Daily Refresh workflow via workflow_dispatch → diagnose from gh run view <id> --log.
Single-county scraper test: county=target, docket_county=target, run_pr=false, commit_results=false. Set commit_results=true only once proven.
"Run completed" does NOT mean "scraper works." Always read the docket-step log content per case.

NEVER FABRICATE DATA — CORE ANTI-FALSE-POSITIVE RULE

If a debt amount cannot be extracted (PDF not found, parse failed, ambiguous): field stays 0, lead classified unknown — NEVER green, NEVER confirmed.
A scraper that runs clean but returns a guessed/wrong number is worse than one that fails loudly. Fail loudly.
Debt comes from the document or it does not exist. No inference, no estimation.
A prayer/debt amount equal to the opening bid is a RED FLAG — that is the Ohio 2/3-appraised trap. Reject it.
The returned case number must match the searched case number.

ERIC'S 6 CORRECTIONS (client-defined — violating these undoes prior work)

PropertyRadar is a lien REPORT, not a kill switch. Only reports 2nd-position liens/HELOCs. Never confirms or kills a lead.
Ohio opening bid = statutory 2/3-appraised value, NOT real debt. Real OH debt = docket prayer/writ/final-judgment amount only.
Florida is "one tier" — FL opening bid IS the real debt.
Killed leads are filtered OUT of the deliverable entirely — not badged, not kept.
Docket is primary for Ohio.
Only Cuyahoga shows the prayer amount as a field. Franklin, Montgomery, Summit, Hamilton require opening the summary judgment PDF and extracting debt from it.

THREE-TIER MONEY MODEL — never conflate tiers

confirmed_surplus — docket-verified WITH all proof fields. The only tier that means real money.
estimated_surplus — PropertyRadar enrichment. An estimate.
apparent_surplus — auction math only. Unverified.
confirmed_surplus = $0 is a correct, honest result when nothing is docket-proven. Never inflate it.
The 22-case suite python -m tests.test_verification must stay green (it tests the status model).

CURRENT STATE

HEAD around f8ab3f9 or newer on main. GitHub is the source of truth.
DONE: repo recovery; verification hardening (three-tier model, 22/22 tests); dashboard UI aligned; daily automation (.github/workflows/daily-refresh.yml, 6AM, all counties); dead daily_pipeline.yml removed; workflow v2 with single-county/test-mode inputs; Duval auction URL fixed (realtaxdeed.com to realforeclose.com).
IN PROGRESS: Franklin docket scraper built (core/dockets/franklin.py), registered, first Actions test run done — verify PDF extraction actually works before replicating the pattern.

MAIN TASK — 4 OHIO DOCKET SCRAPERS
Build docket scrapers for Franklin, Montgomery, Summit, Hamilton. This is the deliverable gap — confirmed_surplus is $0 because 9 of 10 counties have no docket layer.

Template: core/dockets/cuyahoga.py (proven). Base class + shared detectors: core/dockets/base.py. Register in core/dockets/__init__.py SCRAPER_REGISTRY.
County configs live in config/counties.py.
Hard part: these 4 counties need summary-judgment PDF extraction (pdfplumber is available), not a structured prayer field.

KNOWN ISSUES (lower priority than the Ohio scrapers)

Dead-browser-context bug in core/auction/universal.py — the day-loop keeps calling page.goto() on a closed page. Should detect a dead context and break.
Miami-Dade docket (core/dockets/miami_dade.py) blocked by site-wide reCAPTCHA v3. Out of scope for the 2-day Ohio push.
Multi-parcel blanket-judgment surplus aggregation — when one judgment secures N parcels (e.g. Summit CV-2025-02-0548 → 12 parcels, one $852K judgment), the per-parcel `true_surplus = sale - debt` math marks each parcel KILLED even when total sale across the case clears the debt. Group same-case parcels and compare aggregate sale to the single prayer.

## PROPERTYRADAR API

Token: `PROPERTYRADAR_TOKEN` is a GitHub Actions secret AND must be exported locally for any local test. There is NO hardcoded fallback. The `9ffe6b0b…0700` token (previously mislabeled "dead" in earlier notes) is the live funded token. The token guard fails loud on missing/empty ONLY — it must never reject a token by prefix.

Request shape (verified against developers.propertyradar.com):
- POST `https://api.propertyradar.com/v1/properties`
- Header: `Authorization: Bearer <token>`
- Body: `{"Criteria": [{"name":"<CriteriaName>","value":[...]}, …]}` — top-level "Criteria" is always an array; each item is a single-criterion object. Nested criteria use the same shape (e.g. `PropertyType: [{"name":"PType","value":["SFR"]}]`). Never send a raw address string at the top level.
- Query params: `Fields` (comma-separated PR field names), `Limit`, `Start`, `Purchase` (see below).

Address-criterion field names for the `/properties` endpoint — empirically verified by Purchase=0 probe (PR returns "Unexpected Criterion: X" with the exact bad name when wrong):
- `SiteAddress` ✓
- `ZipFive` ✓
- `SiteCity` ✗ rejected by `/properties` (works elsewhere; don't send here)
- `SiteState` ✗ rejected by `/properties` (use it only as a suggestion-endpoint scope criterion)
- Best primary lookup: SiteAddress + ZipFive. When ZipFive is missing, fall through to the suggestion endpoint instead of adding city/state to /properties.

Suggestion endpoint `/v1/suggestions/SiteAddress` accepts SiteState as a scoping criterion (and uses the `SiteAddressInput` body field, not the `SuggestionInput` query param).

Purchase parameter — billing-critical:
- `Purchase=0` → counts/RadarID only, returns NO property data, does NOT deduct an export. ALWAYS use this when changing request shape.
- `Purchase=1` → returns full property data, counts as one export per match.
- Workflow rule: any time the request format changes, validate end-to-end with the `pr_probe_address` workflow input first (Purchase=0). Only switch to the normal enrichment step after the probe confirms a non-zero result count for a known address.

Suggestion endpoint (used as a fallback when direct address criteria miss):
- POST `https://api.propertyradar.com/v1/suggestions/SiteAddress`
- Body: `{"SiteAddressInput": "<street>", "Limit": N, "Criteria": [{"name":"SiteState","value":["XX"]}]}` — input field is `SiteAddressInput`, NOT `SuggestionInput`.
- Returns canonical Criteria you re-post to `/properties`.

Local smoke test:
```
PROPERTYRADAR_TOKEN=<token> python -m core.enrichment.propertyradar \
  --probe-address "1253 MCINTOSH AVE|AKRON|OH|44314"
```
This forces Purchase=0 and prints the exact request + response body so any format regression is visible in one run.

SCOPE DISCIPLINE

Narrow, scoped changes. State exactly what changed and what did not.
run.py is dead pre-refactor code — never run it. Real entry points: core.auction.universal, core.dockets.enrich, core.dashboard_data.
dashboard/ (not docs/) is a dead directory — do not touch or delete unless asked.
