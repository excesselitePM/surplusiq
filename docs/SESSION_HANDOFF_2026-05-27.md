# SurplusIQ — Session Handoff 2026-05-27

## 1. Live State

- **Live dashboard:** https://xcerebroai.github.io/surplusiq/
- **Last committed code:** `34b0c3f` (FP-19 Item 3 — header counts reconcile)
- **Last DATA publish:** `6e09048` Daily refresh 2026-05-27 20:30:19 UTC (PRE Item 2+3 data-side fixes)
- **In-flight publish run:** `26537993773` (Daily Refresh, commit_results=true) — still `in_progress` at handoff time. When it lands, it commits a NEW `docs/data/leads.json` + `summary.json` carrying the FP-18/FP-19 changes.

## 2. Current Published State (as of 6e09048)

- 42 visible leads (post-filter), pre-filter raw=55, killed=13 filtered, 0 below-floor
- 📋 Docket-Verified Positive: **7 leads / $233,700** (will rise to **~$260,693** after run 26537993773 publishes — the two near-zero display-bug leads jump $0→$11,461 and $2,800→$17,532)
- Confirmed Surplus: $0 (honest — no proof-of-disbursement filings yet)
- Pipeline Ready: 0
- Three polish items:
  - **Item 1 (HTML)** — LIVE — Franklin/Hamilton Surplus blanked with "—" + manual-verify tooltip
  - **Item 2 (data)** — code merged `2e9f639`, LIVE after run 26537993773 publishes
  - **Item 3 (data)** — code merged `34b0c3f`, LIVE after run 26537993773 publishes

## 3. THE IN-FLIGHT WORK — Data Integrity Audit (run after publish lands)

Paste-verbatim prompt for next session:

> Stop everything else. Full data integrity audit on EVERY lead on the live dashboard before Eric tests. No sampling — every row. The two recent display bugs (CV2025094689 showing $0, CV2025115614 showing $2,800) proved sampling misses real issues. This audit must surface every anomaly across all 42 leads.
>
> For EACH of the 42 leads, programmatically verify and report:
>
> ═══ 1. INTERNAL CONSISTENCY (catches display bugs like the two we just found) ═══
> - Does best_real_surplus (the displayed number) equal true_surplus when docket data is present? If they differ, that's a display-vs-data mismatch — flag it.
> - Does best_real_surplus equal sale_price − opening_bid for FL leads with no docket data? If not, math error.
> - For OH leads with NO docket data and NO PR enrichment: is surplus blank/null, or does it incorrectly show sale − fake_opening_bid?
> - For Franklin/Hamilton leads (manual-verify): is the surplus column actually blanked per Item 1 fix, or are any still showing a dollar amount?
> - For every lead with docket_verified_positive=true: does it actually have a non-empty prayer_amount > 0 and a non-empty true_surplus > 0?
> - For every lead with the 📋 Docket-checked badge: same check — prayer real, true_surplus real.
>
> ═══ 2. FIELD POPULATION (catches missing-data leads pretending to be complete) ═══
> For each lead, confirm presence and non-emptiness of:
> - case_number, county, state, sale_date, sale_price
> - source_url, clerk_manual_search_url (the two trust-anchor links)
> - For docket-checked leads: prayer_amount, true_surplus, debt_source
> - For FL leads: opening_bid, debt_source = "fl_opening_bid"
> - For PR-enriched leads: pr_owner_name OR a clear no-match flag
>
> If any required field for that lead's tier is blank, flag the lead and which field.
>
> ═══ 3. CLASSIFICATION CORRECTNESS ═══
> - Any lead with money_status = "confirmed_surplus" — verify it actually has proof_of_surplus filing reference. If not, that's a fabricated confirmation.
> - Any lead with money_status = "estimated_surplus" — verify PR returned TotalLoanBalance > 0 for it (the rule from FP-8). If TLB is 0 or missing, that's a stale tier badge.
> - Any lead with the 📋 Docket-checked badge — verify it's NOT classified killed, has classification in {green, yellow, red}.
> - Any lead in classification "killed" still visible — should be filtered out per spec. Flag.
>
> ═══ 4. CROSS-CHECK AGAINST RAW SCRAPED DATA ═══
> For each lead, pull the raw record from data/raw/<county>_<date>.jsonl that produced it. Confirm:
> - The sale_price on the dashboard matches the raw_record sale_price field exactly.
> - The opening_bid on the dashboard matches the raw_record opening_bid field exactly.
> - The address on the dashboard matches what was scraped (ignoring formatting like comma placement).
> - The case_number matches.
> - If anything transformed in the pipeline, the dashboard value must derive correctly from the raw value.
>
> ═══ 5. SOURCE URL VALIDITY ═══
> For each lead's source_url and clerk_manual_search_url:
> - HTTP HEAD probe — confirm 200 or 30x to a real page, NOT 404, 403, or generic error.
> - Does the URL's path/query reference the actual case number when extractable from URL structure?
> - Are any URLs identical across leads that should have unique URLs (sign of a static template bug)?
>
> ═══ 6. SUMMIT DOCKET-VERIFIED DEEP-CHECK ═══
> The 7 Summit docket-checked leads are the headline. For each:
> - Pull the saved judgment PDF text (data/judgments/ or wherever pdfplumber output is cached).
> - Confirm the prayer_amount on the dashboard equals what the PDF text says (verbatim).
> - Confirm the case number on the dashboard matches the PDF's case number.
> - This is the gold-standard check — these 7 leads must be unimpeachable.
>
> ═══ OUTPUT ═══
> For each lead, one row in a table: case_number | county | money_status | issues (or "CLEAN"). Then a summary section:
> - Total leads checked: 42
> - Leads CLEAN: X
> - Leads with issues: Y
> - Critical issues (math errors, fabricated tiers, broken trust-anchor links): Z
> - Cosmetic issues (formatting, missing address only, etc.): W
>
> Then for every flagged lead, the specific issue.
>
> If ANY critical issue exists, that's the headline. Do not proceed to handoff to Eric. Fix critical issues first, re-audit, repeat until 0 critical issues.
>
> Run this audit now. Report the full table. No publish, no Eric handoff, until every lead is either CLEAN or has only cosmetic issues.

## 4. Open Items

- **Data integrity audit** (above) — BLOCKER before Eric handoff
- **Step 3: cron audit** — verify the 6 AM scheduled run auto-commits cleanly without manual workflow_dispatch
- **Step 5: README update** — `docs/README_FOR_ERIC.md` may need refresh to mention the new $5K floor + audit-trail header counts
- **Publish-run watch:** when `26537993773` finishes, re-fetch `https://xcerebroai.github.io/surplusiq/data/leads.json` and confirm: `summary.total_leads_pre_filter` populated, CV2025094689 best_real_surplus=$11,461, CV2025115614=$17,532

## 5. Hard Rules — Carry Forward

- **Anti-fabrication.** If debt cannot be extracted, it stays 0 and the lead stays unknown — never green/confirmed. Failing loudly beats a guessed number.
- **Three tiers only.** `confirmed_surplus` (docket-proven with proof filing) / `estimated_surplus` (PR TLB > 0) / `apparent_surplus` (auction math). No new tiers, no new badges beyond 📋 Docket-checked.
- **One scraper change per verification run.** Edits to `core/auction/*`, `core/dockets/*`, `core/loader.py` that affect scrape/filter/merge logic. Dashboard JS, README, tests, workflow YAML, CSV, badge wording don't count.
- **FL vs OH surplus.** FL opening_bid IS real debt (`debt_source=fl_opening_bid`). OH opening_bid is fake 2/3-appraised — Ohio surplus comes ONLY from the docket prayer.
- **Killed leads are filtered OUT** of the deliverable. Not badged, not greyed.
- **PR is a lien REPORT**, never a kill switch and never a primary surplus source. Only meaningful when `TotalLoanBalance > 0`.
- **Cuyahoga prayer < $10K = implausible**, reject and treat as "no docket data" (court-cost trap).
- **Multi-parcel blanket-judgment** grouping: aggregate same-case parcels and compare aggregate sale to single prayer before marking KILLED.
- **GitHub-first testing.** Never run scrapers locally. `gh run view <id> --log` is the only proof of correctness. "Run completed" ≠ "scraper works."
- **Condition-based waits only.** `wait_for_load_state(networkidle)` > `wait_for_function(count)` > `wait_for_selector(visible)`. NEVER `wait_for_selector(state="attached")` — placeholder-container trap. NEVER blind `sleep`. NEVER `slow_mo`.
- **PropertyRadar request shape:** POST `/v1/properties` with `Criteria: [{"name":"SiteAddress","value":[...]}, {"name":"ZipFive","value":[...]}]`. `Purchase=0` always for format probes. `SiteCity`/`SiteState` are rejected by `/properties` — use the suggestion endpoint instead.
- **PR token guard fails loud on missing/empty ONLY** — never reject by prefix. The `9ffe6b0b…0700` token is funded and live.
- **Regressions reported as headlines, not footnotes.** Discovery time, not recovery time.

## 6. Next Immediate Step

1. Wait for run `26537993773` to land. Background waiter `bbj3jpb00` armed.
2. Pull live `leads.json` + `summary.json`. Confirm three polish items visible.
3. **Run the data integrity audit (Section 3) — every row, no sampling.**
4. Fix any critical issues. Re-audit.
5. Only then write Eric handoff message.
