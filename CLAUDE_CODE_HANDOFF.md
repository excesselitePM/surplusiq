# SurplusIQ — Claude Code Handoff & State Snapshot

**Version `0.9.0`** · authoritative state as of commit `2f81ed3` (2026-06-24).
**Read this fully before doing anything.** The repo shows WHAT the code is; this doc explains WHY, the CURRENT verified state, and WHAT'S NEXT. Everything below was read from the actual code / tests / live `docs/data/leads.json` — items that could not be verified against the repo are marked **(unverified)**.

---

## 0. Version

No prior version scheme existed (no tags, no VERSION file, no CHANGELOG — verified). Established this session: **semver in `/VERSION`, git tag `v0.9.0`**.

- **Why 0.9.0, not 1.0.0:** the system is live and delivering leads across 9/10 counties, FL is substantially complete, but **OH debt extraction still ships known false-positives** (the `max()`-near-keyword extractor — see Open Work #1). Calling it 1.0 would overstate accuracy. 1.0.0 = OH debt extraction rebuilt + validated.
- Increment rule going forward: MINOR for a new county/validation capability, PATCH for fixes within existing capability, MAJOR at 1.0 (OH debt correct) and beyond.

## 1. Project & people

- **Builder:** Quentin Flores (Jarvis LLC). Not a professional developer — give exact, copy-paste-able commands; single blocks, never split.
- **Client:** Eric Richardson (Excess Elite LLC).
- **Product:** SurplusIQ — daily lead-intelligence finding real-estate foreclosure *surplus* opportunities across 10 counties (5 FL, 5 OH).
- **Contract:** $10K flat, paid, signed Apr 11 2026. Past deadline — finishing fast is the priority.
- **Repo:** github.com/xcerebroai/surplusiq · **Live dashboard:** https://xcerebroai.github.io/surplusiq/ (GitHub Pages, source = `main` `/docs`).

## 2. Working rules (hard constraints)

- **GitHub-first. Do NOT run scrapers locally.** Actions is the only environment that proves a scraper. Loop: edit → commit → push → trigger Daily Refresh via `gh workflow run` → diagnose from `gh run view <id> --log`. Pure transforms (`core.dashboard_data`, tests, the loader on committed data) MAY run locally — they're deterministic.
- **Dashboard-is-truth.** "Run completed / green" ≠ "works." Verify the PUBLISHED `docs/data/leads.json` on origin, per-lead, not the run status.
- **One scraper change per verification run.** Bundling two scraper changes makes a regression un-rootcauseable. (Dashboard/loader/test/doc/YAML changes can bundle with one scraper change.)
- **Anti-fabrication.** No extractable figure → field stays 0/None, lead is `unknown`/`apparent`, NEVER green/confirmed. Prefer "unknown" over a guessed number. A prayer == opening bid is the OH 2/3-trap — reject it.
- **Investigation-first.** Ground-truth on REAL data (real PDFs, real API responses) before coding a spec. Never code a spec on faith.
- **Scope discipline.** Narrow changes; state exactly what changed and what didn't.

## 3. CURRENT BUILD STATE — 10 counties (read from code)

Sources: `core/dockets/__init__.py` `SCRAPER_REGISTRY`, `core/dockets/enrich.py` `WORKING_DOCKET_COUNTIES`, `tests/`, live `docs/data/leads.json` (21 leads).

**`WORKING_DOCKET_COUNTIES` (run by cron):** `cuyahoga-oh, montgomery-oh, summit-oh, miami-dade-fl, broward-fl, duval-fl`.
**`SCRAPER_REGISTRY` (8 docket scrapers registered):** cuyahoga, miami-dade, franklin, montgomery, summit, hamilton, broward, duval. (Franklin + Hamilton registered but NOT in cron.)

| County | ST | Validation type | Built | In cron | Acceptance test | Live | Known gap |
|---|---|---|---|---|---|---|---|
| Miami-Dade | FL | Docket (flag-based; reCAPTCHA v3 passes) | ✅ | ✅ | `test_miami_dade_docket` **10/10** | 4 | — |
| Broward | FL | Docket (flag-based; public endpoint) | ✅ | ✅ | `test_broward_docket` **35/35** | 3 | NOA over-flags deferred (memory) |
| Duval | FL | Docket (flag-based; public login) | ✅ | ✅ | `test_duval_docket` **29/29** | 3 | — |
| Lee | FL | **PR-FIRST lien** (not docket) | ✅ | ✅ | `test_lee_liens` **35/35** | 0* | Portal walled headless; docket layer parked |
| Orange | FL | **BLOCKED** (reCAPTCHA v2 image) | ❌ | ❌ | — | 4 | Auction+PR only; no docket validation |
| Cuyahoga | OH | Docket (structured prayer field + $10K floor) | ✅ | ✅ | none dedicated (status model only) | 3 | No dedicated acceptance test |
| Montgomery | OH | Docket (PDF `max()` extract) | ✅ | ✅ | none dedicated | 0 | Shared `max()` extractor false-positives |
| Summit | OH | Docket (PDF `max()` extract + $10K prayer floor) | ✅ | ✅ | none dedicated | 2 | **`max()` extractor false-positives — Open #1** |
| Franklin | OH | Docket scraper exists but **Cloudflare-blocked** → PR-fallback | ⚠️ | ❌ | none | 1 | No docket; manual-verify link only |
| Hamilton | OH | Same as Franklin — Cloudflare-blocked → PR-fallback | ⚠️ | ❌ | none | 1 | No docket; manual-verify link only |

\* Lee = 0 live only because no Lee auctions are in the 14-day window now; the gate is wired and tested.
**No dedicated acceptance test exists for any OH county** (cuyahoga/summit/montgomery/franklin/hamilton) or Orange — they're exercised only via `test_verification` (the 32-check status model). **(gap)**

## 4. Dashboard data pipeline (loader → dashboard_data → leads.json)

`core/loader.py:load_all_leads` → `_parse_lead` (state-aware surplus: FL `fl_opening_bid`, OH-tax `oh_tax_minimum_bid`, OH-mortgage `None`) → filters (third-party, min-surplus w/ FP-11 docket-rescue [now prayer ≥ $10K] + OH-no-debt 1.5× overbid gate, sale-date window 14d) → `_apply_docket_to_lead`.
→ `core/dashboard_data.py:export_dashboard_data` → `_surplus_for_payload` (real-debt recognizer incl `prayer_field`/`oh_tax_minimum_bid`; OH-mortgage-no-debt ⇒ `(None, "oh_unverified")`) → `_reassign_status_after_pr` (estimated requires real PR TLB>0) → `_apply_lee_lien_verdict` → FP-14 kill filter → FP-18 $5K floor (exempts `oh_unverified`) → `docs/data/leads.json` + `summary.json` → committed → Pages.
Frontend `docs/index.html`: `renderSurplusCell` (OH `oh_unverified` ⇒ "—"), Owner column reads docket `owner_name` first then `pr_owner_name`.

## 5. Eric's 6 corrections (May 12 call — govern all build decisions; NOT in repo)

1. **PropertyRadar is a lien REPORT, not a kill switch** — reports 2nd-position liens/HELOCs; never confirms/kills.
2. **OH opening bid is garbage** — statutory 2/3-appraised, not debt. Real OH debt = docket prayer/writ/final-judgment.
3. **FL is "one tier"** — FL opening bid IS the judgment/real debt.
4. **Killed leads are filtered OUT** of the deliverable, not badged.
5. **Docket is primary for Ohio.**
6. **Only Cuyahoga shows the prayer as a field.** Franklin/Montgomery/Summit/Hamilton require opening the summary-judgment PDF.

## 6. CHANGES THIS SESSION (with commit hashes)

**FL:**
- Miami-Dade owner-name extraction — `64c8522`; acceptance-test backfill (10/10) — `2fefe6b`.
- Broward owner-name (proper-cased defendant) — `ec5d734`. *(Broward recovery-firm/known-firm gate logic is present and covered by `test_broward_docket` 35/35, but its specific commit predates this session — **unverified hash**.)*
- Duval owner-name — `bd0eeaa`.
- Lee PR-first lien validation: investigation probes `59fe4bc`/`5ecae9d`/`82a5dc1`/`4f4b6e3`; module+tests `1b7bc44`; wire-in `6ef79b5`; cleanup `a2c0828`.
- Dashboard owner-export-gap fix (emit `owner_name` to leads.json) — `c9fff52`.
- Estimated-badge regression fix (tier on real PR refinement, not `owner_name`) — `8b89ea9`.
- Owner-column frontend mapping (docket owner first, PR fallback) — `f5f422f`.

**OH:**
- Opening-bid display fix (Cuyahoga `prayer_field` gate recognized; OH-mortgage-no-debt ⇒ unverified with 1.5× overbid gate; OH-tax keeps Minimum Bid) — `2ce28c8`.
- Summit prayer-plausibility floor ($10K, mirror Cuyahoga; loader rescue ≥ $10K) — `12550b5`.

**Summit debt investigation (throwaway, removed):** `25799f4`/`284e446`/`bfcfbed`/`39a90c0` — captured 5 real judgment PDFs.

## 7. OPEN WORK QUEUE (priority order)

1. **Summit OH debt-extraction REBUILD — investigation DONE, NOT built. THE priority.** The current shared extractor (`core/dockets/montgomery.py:extract_debt_from_pdf_bytes`, used by Summit too) takes the single `max()` dollar figure near a judgment keyword — principal only, no interest/cost/junior-lien math — so it ships **false-positive surpluses**. Proven on real decree: **6973 Van Buren (CV2024125264)** — reads $244,898 principal as "+$32K surplus" vs $277,300 sale, but real debt (interest 4.075% from Sept 2023 on $138,242.97 + late + advances + costs) erases it. **Investigation captured 5 real judgment PDFs** (CV2024125264, CV2025052012, CV2025115614, CV2025105047, CV2019062134). Findings: principal is reliably labeled; modern decrees state interest **rate + from-date** (computable accrued interest on the stated accruing base); late/advances/costs are **un-quantified vague language**; no stated total. **Build scope:** debt ≈ principal + computed accrued-interest-to-sale-date + stated junior liens + a conservative buffer for vague components + a manual-review flag when the decree lacks a computable rate. Watch the split-balance case (interest accrues only on a SUBSET of principal — parse "accruing on the sum of $X").
2. **OH claim/kill polish:** (a) **sale-not-confirmed** kill signal (absent from `core/dockets/base.py` `KILL_SIGNAL_PATTERNS`); (b) **OH recovery-firm name list** (none exists for OH; `COMPETING_FILER_PATTERNS` is generic phrases only); (c) **case-number normalizer** (Summit stores raw + internal `CV-YYYY-MM-####`, NOT the `CV2025-NNNNNN` the client referenced).
3. **Cuyahoga / Montgomery debt treatment** — port Summit's rebuilt extractor; validate on their OWN real decrees (don't assume Summit's format).
4. **PARKED — daily re-verification state model:** track leads over time, resurrect killed leads when conditions change. Needs client rules (tracking window + resurrection conditions) before building.
5. **PARKED — Orange:** blocked by reCAPTCHA v2 image challenge from the datacenter IP (proven). Needs a client decision: residential/proxy IP, Tyler data feed, or leave auction+PR only.

## 8. DURABLE RULES (hard-won — carry forward)

- **Anti-fabrication** (above) — the core anti-false-positive rule.
- **Investigation-first** (above) — captured-real-data before any extraction spec.
- **One-change-per-verification-run** (above).
- **Dashboard-is-truth** (above) — verify published `leads.json`, not run status. Watch the **UTC-midnight FP-7 trap**: a no-scrape rebuild after 00:00 UTC finds no `all_enriched_<today>.json` and correctly drops ALL PR tiers — that's anti-stale behavior, not a bug.
- **Per-county lists are LOCAL.** Recovery-firm name lists differ: Broward ≠ Duval ≠ (likely) each OH county. Never reuse one county's firm list for another without local ground-truth.
- **Per-county quirks:** Cuyahoga prayer-field needs a $10K plausibility floor (fee-noise); Summit now mirrors it; OH opening bid is 2/3-appraised (fake) for MORTGAGE but the real Minimum Bid for TAX (`CVG` cases); blanket-judgment multi-parcel grouping in `core/dockets/enrich.py:run_county`.
- `run.py` is dead — never run it. Entry points: `core.auction.universal`, `core.dockets.enrich`, `core.dashboard_data`. `dashboard/` (not `docs/`) is dead.
- **PropertyRadar token** is a live, funded Actions secret (`PROPERTYRADAR_TOKEN`) — NO local fallback by design; export it for any local PR test. (Earlier "token dead / hardcoded fallback" notes in older docs are obsolete — see `CLAUDE.md` PROPERTYRADAR API section.)

## 9. First moves for a continuing agent

1. `git fetch && git log origin/main -1` (expect a daily-refresh commit on top of `12550b5`); `git status` clean.
2. Read `CLAUDE.md` (full rules) + this doc + the memory index at `~/.claude/.../memory/MEMORY.md`.
3. Run all suites locally: `test_verification` (32), `test_lee_liens` (35), `test_broward_docket` (35), `test_duval_docket` (29), `test_miami_dade_docket` (10).
4. Start Open Work #1 (Summit debt rebuild): re-read the 5 captured decrees' structure (investigation reports in session history), design the extractor against REAL text, build, test on Actions, verify published leads.json.
