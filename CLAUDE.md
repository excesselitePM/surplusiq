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

DEFERRED ENHANCEMENTS (do not action pre-emptively)

PR Documents endpoint — `GET /v1/documents/{DocumentID}` returns document-level lien detail (LienPosition, LienType, LienCourtCaseNumber, LoanPosition) — deeper than the property-level lien flags from the Card fieldset. Evaluate ONLY after the property-level fields (PropertyHasOpenLiens, PropertyHasOpenPersonLiens, NumberLoans, TotalLoanBalance, Persons[]) prove too coarse for Eric's "here's the second positions or liens, or none" report on real run data. Do not build pre-emptively.

KNOWN ISSUES (lower priority than the Ohio scrapers)

Dead-browser-context bug in core/auction/universal.py — the day-loop keeps calling page.goto() on a closed page. Should detect a dead context and break.
Miami-Dade docket (core/dockets/miami_dade.py) blocked by site-wide reCAPTCHA v3. Out of scope for the 2-day Ohio push.
Multi-parcel blanket-judgment surplus aggregation — FIXED in `core/dockets/enrich.py:run_county`. Parcels are grouped by clerk-case-key (per-county parser: Summit→`joined`, Montgomery→`search_text`, Cuyahoga→derived from `case_prefix/year/number`), the docket is scraped ONCE per group, and `true_surplus = aggregate_sale_across_group - prayer`. classify() is called with the aggregate sale, not per-parcel sale. The dashboard still emits one row per parcel (so each parcel's address/sale are preserved) but all rows in a group share the group's classification.

## PROPERTYRADAR API

Token: `PROPERTYRADAR_TOKEN` is a GitHub Actions secret AND must be exported locally for any local test. NO hardcoded fallback. The `9ffe6b0b…0700` token is live and funded (~33,781 free exports as of 2026-05-26). The token guard fails loud on missing/empty ONLY — it must never reject a token by prefix. Earlier CLAUDE.md guidance calling this token "dead" was wrong; so was prior guidance about `SiteAddress`/`SiteCity`/`SiteState` being correct criterion names — those were WRONG field names. Credit use is authorized; do not ask before each Purchase=1 call.

### VERIFIED PRODUCTION CHAIN (Purchase=1 verified end-to-end 2026-05-26 against PBD4D8F5)

```
STEP 1: POST https://api.propertyradar.com/v1/suggestions/SiteAddress
        Query: SuggestionInput="<street>, <city>, <state> <zip>"   (Limit=5)
        Body : {"Criteria": []}                                    (body REQUIRED, array can be empty)
        →    : {"results": [{"Criteria":[{name:Address,value:...},
                                          {name:City,...},
                                          {name:State,...},
                                          {name:ZipFive,...}],
                              "Label":"..."}]}
        Free, no export deducted.

STEP 2: POST https://api.propertyradar.com/v1/properties
        Query: Fields=RadarID, Limit=5, Purchase=0, Start=0   (Purchase REQUIRED)
        Body : {"Criteria": <step-1 Criteria verbatim>}
        →    : {"results": [{"RadarID":"PXXXXXXX"}], "totalResultCount":1}
        Free under Purchase=0 — returns RadarID without burning an export.

STEP 3: GET  https://api.propertyradar.com/v1/properties/{RadarID}
        Query: Fields=Card, Purchase=1                       (Purchase REQUIRED)
        →    : {"results": [{...full Card payload...}]}
        Burns 1 export per call. Card payload includes Owner, AssessedValue,
        TotalLoanBalance, AvailableEquity, AVM, PropertyHasOpenLiens,
        PropertyHasOpenPersonLiens, isFreeAndClear, isCashBuyer, inForeclosure,
        inTaxDelinquency, DistressScore, Persons[] (with PersonHasOpenLiens,
        inBankruptcy, inProbate, isDeceased per person).
```

### CRITERION NAMES (the source of every earlier 400)

The suggestion endpoint returns the canonical names; mirror them:
- `Address`  ✓  (NOT `SiteAddress` — that name produces "Unexpected Criterion")
- `City`     ✓  (NOT `SiteCity`)
- `State`    ✓  (NOT `SiteState`)
- `ZipFive`  ✓

Nothing in the address-lookup chain is plan-gated; every previous "feature not included in your subscription" error was caused by wrong field names. The current plan accepts the chain above.

### PURCHASE PARAMETER (billing-critical)

- `Purchase=0` is preview-only by design — returns counts and (for `/properties` POST) RadarIDs. No record payload. Required on `/properties` POST and `GET /properties/{RadarID}`.
- `Purchase=1` returns full data and deducts 1 export per matched property on `/properties`, 1 export per call on `GET /properties/{RadarID}`.
- Workflow rule: any time the request shape changes, validate with the `pr_probe_chain` workflow input first (it now uses Purchase=1 by default — burns 1 export per probe but proves end-to-end). Free shape-only probes still available via `pr_probe_criteria` (Purchase=0 on POST).

### LOCAL SMOKE TEST

```
PROPERTYRADAR_TOKEN=<token> python -m core.enrichment.propertyradar \
  --probe-chain "1253 MCINTOSH AVE|AKRON|OH|44314"
```
Prints the full three-step trace including the Card payload on success.

### ANTI-FABRICATION

If the chain fails at any step (suggestion has no match, RadarID lookup empty, GET errors), the lead must keep its docket-derived classification or fall back to `apparent_surplus`. NEVER fabricate a `pr_*` field or upgrade a tier without real this-run PR data.

### NO STALE TIERS RULE (FP-7)

`core/dashboard_data.py:_load_pr_enrichment` only loads `all_enriched_<today>.json` — never older files. Any lead missing from today's PR run drops back to its docket-derived tier (or `apparent_surplus` if no docket data). A failed or skipped PR step must NOT leave yesterday's `estimated_surplus` badges sitting on the dashboard. This is the only way `estimated_surplus` can mean "real this-run PR data" rather than "we ran PR once weeks ago and the badge stuck."

### TIER PROVENANCE — estimated_surplus REQUIRES PR TLB > $0 (FP-8)

`estimated_surplus` means "PR refined the SURPLUS NUMBER" — not "PR matched the property." A PR match with `TotalLoanBalance == $0` (the dominant case — freshly foreclosed properties haven't propagated through PR's source data yet) drops the lead to `apparent_surplus`. The PR data (owner, lien flags, tax_delinquency, distress score) still attaches as INTEL FIELDS on the lead, but the surplus tier honestly reflects "auction math only."

Enforced in two places:
- `_reassign_status_after_pr` only sets `money_status = estimated_surplus` when `pr_total_loan_balance > 0`.
- `_surplus_for_payload` defensively checks `tlb > 0` even for leads the loader pre-tagged `estimated_surplus`.

Eric's spec is the source: "PropertyRadar is a lien REPORT, not a kill switch / not a surplus source." The tier badge must not imply PR contributed to the dollar figure when it didn't.

### DOCKET-VERIFIED POSITIVE BADGE (FP-9)

A lead earns `docket_verified_positive: True` (and `priority_rank: 1`) when it has:
- A real docket-extracted prayer amount ≥ $10,000
- A positive `true_surplus`
- A classification in {green, yellow, red}

These are the highest-quality leads on the dashboard regardless of tier (most will be YELLOW or RED since proof-of-surplus filings are typically days/weeks behind the sale). `leads.json` is sorted by `priority_rank` ascending so docket-verified positives surface above PR-matched apparent leads. The dashboard renders a "🎯 Verified" badge next to the classification.

### CUYAHOGA PRAYER-FIELD PLAUSIBILITY FLOOR

`core/dockets/cuyahoga.py:_scrape_summary_page` rejects any `Prayer Amount` value below $10,000 as implausible for a foreclosure judgment. For many older Cuyahoga cases the "Prayer Amount" field on the case-summary page holds court costs / filing fees / small-claim amounts (typical range $100–$3K), not the actual judgment principal. Below-floor values are logged and `prayer_amount` stays 0 so downstream `true_surplus` math doesn't credit fee noise as a real judgment.

### RECENCY FILTER (sale-date, not case-filing-date)

`core/loader.py:load_all_leads()` enforces a hard 14-day window on the sale/auction date, NOT the case-filing date. A case can be filed in 2023 and auctioned last week — what matters is `_extract_sale_date()`, which pulls from `sale_date` / `sale_datetime` / `auction_date` / `soldDate` / `AUCTIONDATE` fields written by the scraper at point of sale. Never use a case number as a date source.

### AUCTION SCRAPER PARALLELIZATION

`core/auction/universal.py:run_all()` runs counties concurrently via `asyncio.gather` bounded by a `Semaphore`. Defaults:
- **Headless runs (CI): cap = 3.** Five+ OH counties share the Grant Street backend at `sheriffsaleauction.ohio.gov`; higher concurrency risks CDN rate-limiting from a single runner IP. The GitHub Actions standard runner (7 GB RAM, 2 vCPU) handles 3 concurrent Chromium contexts comfortably; 5+ with xvfb starts squeezing.
- **Headed runs (local): cap = 1 (serial).** Some scrapers pause for `input()` on CAPTCHA / EULA flow — overlapping prompts from concurrent scrapers would be unusable.
- **`PARALLEL_SCRAPERS` env var** overrides the cap (clamped 1–10) for debugging.
- **Per-county isolation**: `asyncio.gather(return_exceptions=True)` captures crashes per county; failures are logged and the batch continues. One county's crash never aborts the others.
- Wall time impact: ~79 min (sequential) → ~31 min (cap=3, 2.57× speedup). Ceiling is the slowest single county since wall time = `max(per_county_time)` when one county exceeds total work / cap.

### DOCKET SCRAPER PARALLELIZATION

`core/dockets/enrich.py:run_counties_parallel()` mirrors the auction-step pattern:
- **`docket_county` workflow input** accepts a single county-id, a comma-separated list, or the special value `all_working` (expands to the `WORKING_DOCKET_COUNTIES` constant: `cuyahoga-oh, montgomery-oh, summit-oh`). The default `auto` resolves to `all_working` so the Daily Refresh runs every verified docket scraper in one pass.
- **Concurrency cap = 3 in CI, 1 headed.** Same rationale as auction step (CDN, runner RAM, headed input-prompt collisions).
- **`PARALLEL_DOCKETS` env var** overrides (clamped 1–10).
- **Per-county isolation** via `asyncio.gather(return_exceptions=True)`. A docket scraper crash never aborts the batch; other counties still save.
- Loader picks up any `data/dockets/*_*.jsonl` file at dashboard-regen time — no today-only restriction — so docket data from prior runs merges naturally with this run's output.

To add a new docket scraper to the parallel default: prove it on real Actions runs first, then append the county-id to `WORKING_DOCKET_COUNTIES` in `core/dockets/enrich.py`.

### STATE-AWARE SURPLUS RULE (FL vs OH opening_bid)

Per Eric's May 12 call:
- **Ohio** — `opening_bid` is the **statutory 2/3-appraised value, NOT real debt**. The only valid OH debt figure is the docket prayer/writ/judgment amount. No prayer ⇒ `true_surplus = None`. Enforced in `core/loader.py:_merge_docket_data`.
- **Florida** — `opening_bid` **IS the judgment amount** (set from the FL auction calendar). Real-debt math is `sale_price − opening_bid`. **AUDIT FINDING (2026-05-27):** the loader does NOT yet wire this rule through — FL leads with no docket scraper get `true_surplus = None` and tag `apparent_surplus`, blending them with auction-only OH leads. FL leads are under-tiered as a result.
- **Caveat regardless of state:** auction math alone is never `confirmed_surplus`. Confirmation still requires a docket kill-signal check (motion to vacate / bankruptcy / dismissal / proof of disbursement / etc.).

Fix to FL under-tiering is APPROVED IN PRINCIPLE but NOT YET IMPLEMENTED — requires owner sign-off on tier-name and `debt_source` value before changing `_merge_docket_data`. See conversation 2026-05-27 audit report.

SCOPE DISCIPLINE

Narrow, scoped changes. State exactly what changed and what did not.
run.py is dead pre-refactor code — never run it. Real entry points: core.auction.universal, core.dockets.enrich, core.dashboard_data.
dashboard/ (not docs/) is a dead directory — do not touch or delete unless asked.
