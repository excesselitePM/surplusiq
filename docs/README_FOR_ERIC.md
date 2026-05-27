# SurplusIQ — How to Read the Dashboard

This is a one-page guide. Read it once and you'll know what every number on the dashboard means and how to use it.

The live dashboard: **https://xcerebroai.github.io/surplusiq/**

It refreshes automatically every morning at 6 AM Central. Each row is one foreclosure auction lead from the last 14 days.

---

## The columns, left to right

| Column | What it means |
|---|---|
| **Surplus** | The dollar number that matters. How it's calculated depends on the **Money Status** column. |
| **Money Status** | The tier — see "Three tiers" below. |
| **Docket** | What the court docket says about the case (Green / Yellow / Red / Killed / —). A **📋 Docket-checked** badge here is the highest-quality signal — the debt figure came from a real foreclosure judgment, not estimated. |
| **Tier** | A simple A+ / A / B / C grade based on the surplus size. Use this to scan for the biggest opportunities first. |
| **County / St** | Where the auction happened. |
| **Case #** | The court case number. Copy this when you call the clerk. |
| **Address** | The foreclosed property's street address. |
| **Sale Price** | What it sold for at auction. |
| **Opening Bid** | Starting bid at auction. **In Florida this is the actual judgment amount** — that's why FL surplus math is `sale - opening`. In Ohio this is a fake statutory number (2/3 of appraised value) and **does NOT represent real debt** — Ohio surplus only comes from the docket. |
| **Sold At** | Date and time of the auction. Use this to verify recency at a glance. Anything you see on the dashboard sold within the last 14 days. |
| **Owner** | The property owner's name (from PropertyRadar enrichment). Use this for skip-tracing and outreach. |
| **Evidence Level** | How verified the data is. `docket_confirmed` is the strongest. `property_enriched` means PropertyRadar matched. `auction_only` means auction math only. |
| **Source** | Link to the auction site listing. |
| **Clerk Docket** | One-click link to the county clerk's case-search portal. **"Verify ⚠"** for Franklin/Hamilton means the docket couldn't be automated — click through to verify the case manually before acting. **"Open ↗"** for other counties means the case detail is reachable but not the only source of truth. |
| **Sold To** | "3rd Party Bidder" = potential surplus to claim. "Plaintiff" = the foreclosing lender bought it back. |

---

## Three tiers — what each one means

### 🟢 `confirmed_surplus`
The court records confirm there's surplus money to claim. This is the gold standard — a real prayer/judgment amount AND a proof-of-disbursement filing (notice of excess proceeds, certificate of disbursement). Currently rare — these filings happen days or weeks after the sale, so most dashboard leads start in lower tiers and get promoted to confirmed as the court paperwork catches up.

### 🟡 `estimated_surplus`
PropertyRadar returned a real loan balance for this property, and we computed `sale price - loan balance` as the surplus estimate. **Only meaningful when PropertyRadar's TotalLoanBalance is greater than zero.** If PR returned $0 loan balance, the lead drops to apparent (see next section) — we don't pretend PR refined the math when it didn't.

### ⚪ `apparent_surplus`
Auction math only — `sale price - opening bid`. For Florida leads this IS real debt math (opening bid is the judgment in FL). For Ohio leads this is unverified arithmetic on a fake number until the docket reveals the real debt. Treat apparent_surplus as "interesting, needs verification" — not as a number to act on without a docket check.

### Special highlight: 📋 Docket-checked
A badge that appears next to the Docket column on certain leads. It means: the debt figure on this lead came from a **real prayer amount extracted from a foreclosure judgment PDF**, the math `sale - prayer` is positive, and the case is not killed. These are still classified `apparent_surplus` (not confirmed — they're waiting for the proof-of-disbursement filing), but they're the highest-quality leads on the dashboard. Sort by Surplus and look for 📋 first.

---

## Which counties are scraping vs PR-fallback

| County | State | How we get docket data |
|---|---|---|
| Cuyahoga | OH | ✅ Full docket scrape (prayer field) |
| Montgomery | OH | ✅ Full docket scrape (PDF extraction) |
| Summit | OH | ✅ Full docket scrape (PDF extraction) |
| Broward | FL | Auction-only + PropertyRadar |
| Duval | FL | Auction-only + PropertyRadar |
| Lee | FL | Auction-only + PropertyRadar |
| Miami-Dade | FL | Auction-only + PropertyRadar (docket portal blocked by reCAPTCHA) |
| Orange | FL | Auction-only + PropertyRadar |
| **Franklin** | **OH** | ⚠ **Cloudflare-blocked.** PropertyRadar only. Click "Verify ⚠" in the Clerk Docket column to check the docket manually. |
| **Hamilton** | **OH** | ⚠ **Cloudflare-blocked.** PropertyRadar only. Click "Verify ⚠" in the Clerk Docket column to check the docket manually. |

The 3 OH counties with full docket scraping (Cuyahoga, Montgomery, Summit) are the ones producing 📋 Docket-checked leads.

---

## Known limitations — read this before testing

### PropertyRadar lags fresh foreclosures
PR's data refreshes on a delay. Properties that hit foreclosure auction recently often appear in PR as **free-and-clear with $0 loan balance** — even though they're obviously in foreclosure (because PR hasn't seen the new lender's records yet). **Don't trust PR's TotalLoanBalance as a current debt source for freshly-foreclosed property.**

What PR is good for on these leads:
- **Owner name** — for skip-tracing
- **Owner mailing address** — usually accurate even when loan data is stale
- **PropertyHasOpenLiens / PropertyHasOpenPersonLiens flags** — non-mortgage liens (mechanic's, judgment, tax) that PR does see
- **inTaxDelinquency, isFreeAndClear, DistressScore** — qualitative signals

What PR is NOT good for here:
- The surplus dollar number on freshly-foreclosed property
- Confirming whether the lead has competing liens — use the docket for that

### The 14-day window is by sale date, NOT case-filing date
A case filed in 2023 can have an auction last week. The recency filter uses the actual sale/auction date, not the case number. So you won't miss recent activity on old cases.

### Cuyahoga's "prayer field" is unreliable for older cases
For many older Cuyahoga foreclosures (especially TREASURER vs … tax foreclosures), the "Prayer Amount" field on the case-summary page holds court costs or filing fees ($100–$3K), not the actual judgment. We reject any Cuyahoga prayer under $10K as not-found and treat it the same as "no docket data." Better to honestly say "unknown" than credit a $500 court-cost number as if it were the foreclosure debt.

### Some leads may show a blank address
If the source auction page didn't include a property address, the dashboard honestly shows blank rather than guessing. The case number, sold-to, and clerk-portal link are still authoritative — click through to verify.

### Killed leads are not shown
Per spec, any lead with a kill signal (motion to vacate, bankruptcy, sale vacated, owner already filed claim, funds already disbursed, escheated to state) is filtered out of the dashboard entirely. The raw docket data stays on disk for audit, but you won't see those leads on the dashboard — they have zero actionable surplus opportunity.

---

## How to read each lead for outreach

1. **Filter by 📋 Docket-checked first.** Sort by Surplus descending. These are leads where the surplus math is backed by a real judgment.
2. **Check the Sold At date.** Anything more than 3-4 days old, the owner may have already been approached by competing claim filers.
3. **Click Address → Google.** Verify the property exists and the address parses cleanly.
4. **Click Clerk Docket → portal.** Read the actual docket entries before contacting the owner. Look specifically for:
   - "Motion to vacate sale" — kill signal
   - "Notice of bankruptcy" — kill signal
   - "Motion for surplus funds" / "Claim for surplus funds" — someone else already filed
   - "Certificate of disbursement" — funds already paid out
5. **Owner field gives you the skip-trace starting point.** Cross-reference against PropertyRadar's mailing address (in the leads.json), public records, social.
6. **For Florida leads:** the surplus math is real-debt-backed, but you still need a docket check before treating it as confirmed. The clerk portal link in the Clerk Docket column is the fastest way to that.

---

## Questions / something looks wrong

The whole pipeline is open-source. The README in the repo at `github.com/xcerebroai/surplusiq` has technical detail on every fix and every classification rule (CLAUDE.md is the working memory).

If a lead's surplus number looks wildly off, the first thing to check is **which tier it's in.** A $400K apparent_surplus lead on a property that obviously has a $300K mortgage is auction math doing its job — apparent_surplus is not the final word, just the starting point.
