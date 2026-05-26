# SurplusIQ — Claude Code Handoff

**Read this fully before doing anything. The repo shows WHAT the code is; this doc explains WHY and WHAT'S NEXT.**

---

## 1. Project & people

- **Builder:** Quentin Flores (Jarvis LLC). Not a professional developer — prefers exact, copy-paste-able commands and clear explanations.
- **Client:** Eric Richardson (Excess Elite LLC).
- **Product:** SurplusIQ — a daily lead-intelligence system that finds real-estate foreclosure *surplus* opportunities across 10 counties (5 FL, 5 OH).
- **Contract:** $10K flat, paid upfront, signed April 11 2026. Originally 4-week delivery. **Now past deadline — finishing within ~2 days is the goal.**
- **Repo:** github.com/xcerebroai/surplusiq · **Live dashboard:** https://xcerebroai.github.io/surplusiq/
- **Current HEAD:** `b424616` on `main`. GitHub is the complete source of truth — there is no uncommitted work.

## 2. Working rules (hard constraints)

- **GitHub-first. Do NOT run scrapers locally.** Eric's system runs on GitHub Actions; that is the only environment that proves anything. Workflow: edit code → commit → push → trigger the Actions workflow → diagnose from Actions logs (`gh run view --log`). Every run leaves a trail.
- **Execution mode.** No session timestamps, no break/stopping-point suggestions, no padding. Direct technical work only. Hard pushback when warranted is welcome.
- **Scope discipline.** Narrow, scoped changes. State exactly what changed and what didn't.

## 3. The 10 counties

FL: miami-dade, broward, duval, lee, orange — OH: cuyahoga, franklin, montgomery, summit, hamilton.

## 4. Eric's 6 corrections (May 12 call — these govern all build decisions)

These are NOT in the repo — they come from a client call. Violating them undoes prior work.

1. **PropertyRadar is a lien REPORT, not a kill switch.** It only checks for 2nd-position liens / HELOCs and reports found-or-none. It never confirms or kills a lead. (Already implemented correctly.)
2. **Ohio opening bid is garbage** — it's the statutory 2/3-of-appraised-value figure, not real debt. Real OH debt = the docket **prayer / writ / final-judgment amount**. (Loader already treats OH opening bid as fake; see #6 below for the gap.)
3. **Florida is "one tier"** — the FL opening bid IS the judgment/real debt. (Already handled.)
4. **Killed leads are filtered OUT** of the deliverable, not badged. (Implemented.)
5. **Docket is primary for Ohio.**
6. **Only Cuyahoga shows the prayer amount on the case page.** Franklin, Montgomery, Summit, Hamilton require opening the **summary judgment document (PDF)** and reading the debt amount from it. This is the unbuilt work.

## 5. What is DONE

- Repo recovered & reproducible.
- **Verification hardening** — false-positive logic eliminated. `true_surplus` is docket-only. Three-tier money model: `confirmed_surplus` (docket + proof fields) / `estimated_surplus` (PR enrichment) / `apparent_surplus` (auction math only). 7-case test suite, **22/22 passing** (`python -m tests.test_verification`).
- **Dashboard UI** reads the hardened `summary.json` — confirmed-surplus-first, $0 shown honestly.
- **Daily automation** (`.github/workflows/daily-refresh.yml`) runs 6 AM, scrapes all 10 counties, regenerates the dashboard, commits. 9 of 10 counties scrape successfully.
- Dead `daily_pipeline.yml` workflow removed (it ran the broken `run.py`).
- **Workflow v2** — `daily-refresh.yml` now has `workflow_dispatch` inputs: `county` (single-county or `all`), `run_dockets`, `run_pr`, `docket_county`, `commit_results`. This enables fast single-county test runs via Actions with no junk commits.
- **Duval auction URL fixed** (`b424616`) — was pointed at the tax-deed portal `realtaxdeed.com`; now `realforeclose.com` like other FL counties. NOT yet verified by an Actions run.

## 6. THE MAIN TASK — 4 Ohio docket scrapers

**`confirmed_surplus_total` has been $0 the entire time. That is correct, not a bug** — confirmed surplus requires a docket match with proof fields, and only 1 of 10 counties (Cuyahoga) has a docket scraper. Building the docket layer for the other counties is the deliverable gap.

**Target: build docket scrapers for Franklin, Montgomery, Summit, Hamilton (OH).**

- **Template:** `core/dockets/cuyahoga.py` — a working, proven docket scraper. Copy its structure.
- **Base class:** `core/dockets/base.py` — `DocketScraper`, `DocketResult`, `DocketEvent`, plus shared kill-signal / proof-of-surplus / competing-filer detection and the `classify()` method.
- **Registry:** add each new scraper to `SCRAPER_REGISTRY` in `core/dockets/__init__.py`.
- **Key difference from Cuyahoga:** these 4 counties do NOT show the prayer amount as a structured field. The scraper must locate the **summary judgment** docket entry, open its **PDF**, and extract the debt amount from the PDF text. This is the hard part — each county's clerk portal differs.
- **County config** is in `config/counties.py` — each `CountyConfig` has `clerk_search_url`, `clerk_system`, `case_format`, etc.

## 7. Testing loop (GitHub Actions only)

1. Write/edit scraper code, commit, push.
2. Trigger the workflow: Actions tab → "Daily Refresh" → "Run workflow" → set `county` to the target county, `docket_county` to the target county, `run_pr` = false, `commit_results` = false. (Or `gh workflow run`.)
3. Read the log: `gh run view <run-id> --log` — diagnose from there.
4. Iterate. Only set `commit_results` = true once a scraper is proven.

## 8. Other known open items (lower priority than the Ohio scrapers)

- **Dead-browser-context bug** in `core/auction/universal.py` — when the browser context dies mid-run, the day-loop keeps calling `page.goto()` on a closed page (`⚠ Load error: ... has been closed` repeating). Should detect a dead context and break. Affects any county, not just Duval.
- **Miami-Dade docket** — `core/dockets/miami_dade.py` exists but is blocked by site-wide reCAPTCHA v3. Not in scope for the 2-day Ohio push.
- **PropertyRadar token** — hardcoded fallback in `core/enrichment/propertyradar.py:58` and `_archive/enrichment/enrichment.py:18`. Quentin has explicitly accepted this risk twice — do NOT action it unless he explicitly asks. Token also has 0 credits.
- `run.py` is dead pre-refactor code — never run it. Real entry points: `python -m core.auction.universal`, `python -m core.dockets.enrich`, `python -m core.dashboard_data`.
- `dashboard/` (no `docs/`) is a dead directory — do not touch, do not delete unless asked.

## 9. First moves for this session

1. Confirm `git log -1` shows `b424616` or newer; `git status` clean.
2. Run `python -m tests.test_verification` — expect 22/22.
3. Read `core/dockets/cuyahoga.py`, `core/dockets/base.py`, `core/dockets/__init__.py` fully.
4. Read the Franklin/Montgomery/Summit/Hamilton blocks in `config/counties.py`.
5. Begin with ONE county (Franklin suggested) — recon its clerk portal, build the scraper, test via Actions, then replicate the pattern to the other three.
