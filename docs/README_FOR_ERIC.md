# SurplusIQ — Quick Guide

SurplusIQ is a daily lead-intelligence dashboard for foreclosure surplus opportunities across 10 counties — Florida (Miami-Dade, Broward, Duval, Lee, Orange) and Ohio (Cuyahoga, Franklin, Hamilton, Montgomery, Summit). Each row is one auction sale from the last 14 days. The pipeline refreshes automatically every morning at **6:00 AM Central / 7:00 AM Eastern**. No manual triggering required.

**Live dashboard:** https://xcerebroai.github.io/surplusiq/

---

## Headline numbers — what the four KPI cards mean

The cards run left to right in order of actionability.

### 📋 Verified Surplus *(the one that matters)*
Real prayer amount extracted from a foreclosure judgment PDF, positive surplus math (`sale − prayer > 0`), and no kill signals on the docket. **These are the dashboard's actionable leads today.** They are court-verified — the prayer figure was pulled verbatim from the judgment document, not inferred. Filter to these first.

### Apparent Surplus
Auction math only: `sale price − opening bid`.

- **For Florida leads**, the opening bid IS the judgment amount — so this math is real-debt-backed.
- **For Ohio leads**, the opening bid is the statutory 2/3-appraised value and does NOT represent real debt. OH Apparent Surplus rows show "—" in the Surplus column intentionally; use the Clerk Docket link to verify the real judgment before acting.

Treat Apparent Surplus as "interesting, needs verification" — not as a number to act on without a docket check.

### Estimated Surplus
PropertyRadar refined the loan balance and we computed `sale − loan balance`. Only meaningful when PR returned a non-zero balance. Rare on fresh foreclosures because PR's data lags new lender records by weeks. If PR returned $0 balance, the lead falls back to Apparent.

### Disbursement Filed
The court has posted a notice of excess proceeds or certificate of disbursement. By the time this filing appears, the lead has typically aged out of the 14-day window — so this card normally reads $0 / 0. Included for completeness; when something flips it, that's a Verified lead the court has already paid out.

---

## How to use the dashboard

1. **Start with the 📋 Verified leads.** They sort to the top automatically. These are your highest-quality opportunities each day.
2. **Click any "View" or "Open ↗" or "Verify ⚠" link.** The case number auto-copies to your clipboard, the link opens in a new tab, and a small toast confirms it. Paste the case number into the clerk's search field — clerk portals don't support URL deep-linking, so this is the smoothest path.
3. **Use the filter bar** to drill by state, county, tier, or free-text search (matches address, case number, parcel ID, county name).
4. **CSV export** — the button below the table exports the currently-filtered, currently-sorted view with all key fields including the provenance columns (debt source, PR loan balance, true surplus) so you can audit any row offline.

### What each lead row tells you

| Column | What it is |
|---|---|
| **Surplus** | The dollar number. Calculated per the tier above. OH rows without a real docket show "—". |
| **Money Status** | Which tier the lead sits in. |
| **Docket** | Court docket classification. **📋 Verified** is the highest-quality signal. |
| **Tier** | A / B / C grade by surplus size. |
| **Case #** | The court case number. Auto-copied on link clicks. |
| **Address** | Property address (blank if the auction site didn't publish one). |
| **Sale Price / Opening Bid** | Auction figures. |
| **Sold At** | Date and time. Everything visible is within the last 14 days. |
| **Owner** | From PropertyRadar enrichment. Blank when PR didn't match. |
| **Evidence Level** | Strongest = `docket_confirmed`; weakest = `auction_only`. |
| **Source** | Auction-site listing for the sale date. |
| **Clerk Docket** | "Open ↗" for portals we link normally; "Verify ⚠" for Cloudflare-blocked counties (Franklin, Hamilton) where the docket couldn't be automated. |
| **Sold To** | "3rd Party Bidder" = potential surplus to claim. "Plaintiff" = lender bought it back (no surplus path). |

---

## County coverage

| County | State | What's automated | What needs manual verify |
|---|---|---|---|
| Miami-Dade | FL | Auction + opening-bid math (real FL debt) | Docket detail (Phase 2 — reCAPTCHA-blocked today) |
| Broward | FL | Auction + opening-bid math | Docket detail via Clerk Docket link |
| Duval | FL | Auction + opening-bid math | Docket detail via Clerk Docket link |
| Lee | FL | Auction + opening-bid math | Docket detail via Clerk Docket link |
| Orange | FL | Auction + opening-bid math | Docket detail via Clerk Docket link |
| Cuyahoga | OH | Auction + **docket scrape + prayer extraction** | None (full automation) |
| Montgomery | OH | Auction + **docket scrape + PDF prayer extraction** | None (full automation) |
| Summit | OH | Auction + **docket scrape + PDF prayer extraction** | None (full automation) |
| Franklin | OH | Auction only | **Docket — Cloudflare-blocked. Click "Verify ⚠".** |
| Hamilton | OH | Auction only | **Docket — Cloudflare-blocked. Click "Verify ⚠".** |

The 3 OH counties with full docket scraping (Cuyahoga, Montgomery, Summit) are the ones currently producing 📋 Verified leads.

---

## In scope today vs. Phase 2

**Shipped and running daily:**

- Auction tracking across all 10 counties
- Docket scrape + PDF prayer extraction for 3 OH counties (Cuyahoga, Montgomery, Summit)
- Surplus detection with anti-fabrication guard (no debt figure = no Verified tier — never guessed)
- Daily refresh at 6:00 AM Central, fully automated
- CSV export of any filtered view
- Clipboard-aware verify links on every row
- Kill-signal filtering (vacated sales, bankruptcies, prior claims, escheated funds — removed from view, not badged)

**Phase 2 (separate scope, not in this build):**

- PropertyRadar enrichment at scale — currently paused pending token funding for daily volume
- Day 3 / Day 7 / Day 14 rescan scheduler — automatically re-check leads as the docket evolves
- Excess Elite CRM dedup integration — push qualified leads directly into your CRM, skip already-worked records
- Miami-Dade docket scraper — reCAPTCHA-blocked today
- Franklin and Hamilton docket scrapers — Cloudflare-blocked today

---

## Known limitations — read this before testing

**Clerk portal deep-linking is not possible.** Every county uses a stateful search form (SPA, ASP.NET ViewState, or Tyler CMS) that does not accept URL query parameters. The clipboard-copy workaround eliminates re-typing — click the link, paste the case number, you're on the right record.

**Cron is UTC-fixed; daylight savings shifts the local clock.** The 6:00 AM Central fire is exact during CDT (summer). When CST kicks in (November–March), the same UTC time becomes 5:00 AM Central. Not a bug, just a calendar effect.

**PropertyRadar enrichment is paused.** Owner names and lien flags only attach when PR is funded for daily runs. Without it, the dashboard still produces Verified leads from the OH docket — PR adds skip-trace data, not core surplus detection.

**The 14-day window is by sale date, not case-filing date.** A case filed in 2015 can have an auction last week — what matters is when it sold, not when it was filed.

**Cuyahoga's prayer field has a $10K plausibility floor.** Below that, the "Prayer Amount" on the case summary often holds court costs / filing fees, not real judgment debt. We reject sub-$10K values as not-found rather than credit them as judgments.

**Killed leads are removed from the dashboard entirely.** Motions to vacate, bankruptcy filings, sale vacated, owner already filed claim, funds already escheated — these leads have no actionable surplus opportunity and are filtered out rather than badged. The raw data stays on disk for audit.

**Some leads may show a blank address.** When the source auction page omitted an address, we honestly show blank rather than guess. The case number and Clerk Docket link still get you to the authoritative source.

---

## Feedback

Two tier labels are new in this build:

- **Verified Surplus** — what we used to call "Docket-Verified Positive." Same data, cleaner name.
- **Disbursement Filed** — what we used to call "Confirmed Surplus." Renamed because in your industry "confirmed" tends to mean "court has paid out," which happens weeks after sale and falls outside our freshness window.

If the surplus-recovery industry uses different conventions for either, tell us and we'll adjust the labels — the underlying data won't change.

Anything missing, unclear, or that disagrees with how you work day-to-day: flag it. The pipeline is open-source and the working memory is in `CLAUDE.md` at the repo root if you ever want the technical detail behind any decision.
