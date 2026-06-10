# Broward docket — SURPLUS-CLAIM vocabulary, validated against real cases (2026-06-10)

Source: GitHub Actions run **27312374787** (headless, datacenter IP, zero CAPTCHA).
Probe: `scripts/broward_investigate.py` (extract_docket + scan_surplus).
Evidence: `data/samples/broward/ci/<CASE>_docket.json` (full per-case docket, verbatim).

**Candidate selection:** 22 Broward cases SOLD 30–55 days ago (auction_date ≤ 2026-05-11,
pulled from `data/raw/broward-fl_*.jsonl`), ranked by surplus, biased to 3rd-party
sales — i.e. old enough that post-sale surplus claims have had time to be filed.
**Result: 9 of 22 have real surplus-claim activity.** Vocabulary is now ground-truthed.

---

## HEADLINE — the kill terms are NOT in the Description column

The earlier VOCAB_FINDINGS assumption ("KILL terms would match the `Description`
column verbatim when present") is **WRONG for surplus-claim filings.** Verified:

- `Description` holds **generic clerk labels**: `Motion for Disbursement`,
  `Motion to Intervene`, `Order Granting`, `Order Disbursing Funds`,
  `Emergency Motion`, `Agreed Order`, `Notice of Appearance`.
- The **surplus-specific text lives in the `Additional Text` column**, with
  heavy wording variation and OCR-ish typos (`ASS GNEE`, `INTERVEN`, `D SBURSE`).

**Implication for the build: the classifier MUST scan `Additional Text`, not just
`Description`.** A Description-only matcher would miss every surplus claim in this set.

(The positive/neutral clerk doc-titles — Certificate of Sale, Final Judgment,
Notice of Sale, Lis Pendens, Certificate of Title — DO still appear verbatim in
`Description`. It's specifically the surplus-claim filings that get genericized.)

---

## Eric's 9 spec terms vs. reality

| Eric's spec term | Verbatim in docket? | What's actually there |
|---|---|---|
| Motion for Surplus Funds | **NO** (not as-is) | `DESC: Motion for Disbursement` / `Motion to Intervene` / `Emergency Motion`, with surplus in ADDL |
| Motion to Disburse Surplus | **PARTIAL** — in ADDL | `ADDL: "MOTION TO DISBURSE SURPLUS FUNDS"`, `"to DISBURSE SURPLUS FUNDS"` |
| Claim / Petition for Surplus | **PARTIAL** — in ADDL | `ADDL: "CLAIM TO SURPLUS FUNDS"`, `"Claim For The Surplus Retained By Clerk"`. No "Petition for Surplus" anywhere. |
| Homeowner Claim for Surplus | **NOT FOUND** | No such string. Owner claims arrive as attorney/assignee `Motion to Intervene`, not a "homeowner claim". |
| Certificate of Disbursement | **YES** — but NOISE | `DESC: Certificate of Disbursements` appears on **~every sold case** (admin entry). NOT a surplus-claim signal. See warning below. |
| Order Disbursing Surplus | **NO** (not as-is) | `DESC: Order Disbursing Funds` (generic). Surplus context only in ADDL: `"to Plaintiff (surplus proceeds)"`, `"ORDER DISBURSING THE REMAINING SURPLUS FUNDS"` |
| Assignment of Surplus Rights | **NOT FOUND** as a title | The assignment shows up as the word `Assignee` inside NOA/motion ADDL (`"as Assignee for James A. Le Roux"`, `"Assignee Capital Crafter Inc"`), never as a docket entry titled "Assignment of Surplus Rights". |
| Stipulation for Surplus | **NOT FOUND** | `Joint Stipulation` entries exist but are for consent final judgment, NOT surplus. Do not match on "stipulation". |
| Surplus-company Notice of Appearance | **YES** — rich data | `DESC: Notice of Appearance` (generic); firm name in ADDL. Full breakdown below. |

**Bottom line on exact strings:** match on these substrings in **Additional Text**,
case-insensitive, typo-tolerant (allow an optional space inside words):

- `surplus` (the anchor — almost every real claim row contains it)
- `disburse surplus` / `disbursement of surplus` / `surplus proceeds` / `surplus funds`
- `claim to surplus` / `claim for the surplus`
- `motion to intervene` **+ surplus in same case** (intervene is the owner/assignee entry point)
- `order disbursing funds` **+ surplus context** → strong KILL (money already ordered out)
- `assignee` (assignment signal — a recovery firm took the owner's rights)

**Do NOT match on:** `homeowner claim for surplus`, `petition for surplus`,
`stipulation for surplus`, `assignment of surplus rights` — **none appear in any of
22 real cases.** Don't code strings the docket never emits.

---

## ⚠️ "Certificate of Disbursements" is a TRAP, not a kill signal

`Certificate of Disbursements` (note the plural) shows up in essentially every
sold case — it's the clerk's routine record of paying sale proceeds to lienholders
per the final judgment. It does **not** mean the *surplus* was claimed. If we kill
on "Certificate of Disbursement" we'd kill every lead. **Flag for Eric:** confirm
he doesn't intend this as a kill term, or scope it to "Certificate of Disbursement
+ surplus language in ADDL" only.

---

## Notice of Appearance — surplus-company vs benign (the hard classifier)

`Description` is always generic `Notice of Appearance`. The distinguishing data is
entirely in `Additional Text`. Three real categories from this run:

### A) Surplus-recovery / funding firms → FLAG / KILL (real verbatim ADDL)
```
PRIORITY SURPLUS LLC                                            (COCE-25-085528)
GET LIQUID FUNDING, LLC                                         (CACE-24-012541, CACE-25-005168, COWE-25-085495, COWE-26-005819)
The Recovery Agents, LLC                                        (CACE-24-007420)
AMERIFUND EQUITY GROUP                                          (CACE-24-008631)
New Beginnings Trustee, LLC as Assignee for James A. Le Roux    (CACE-24-008631)
PRESTIGE PROCESSING SERVICES, LLC, as consultant               (CACE-23-015282, in Motion to Intervene)
HOME DEFENSE ALSO KNOWN AS EVO RECOVERY CONSULTATION CORP ...   (CONO-25-048381)
Capital Crafter Inc. / as counsel on behalf of Assignee Capital Crafter Inc  (CACE-14-020395, CACE-25-002358)
```
Self-identifying keywords observed: **SURPLUS, FUNDING, RECOVERY, ASSIGNEE,
TRUSTEE, PROCESSING SERVICES, CONSULTATION, EQUITY GROUP**.

### B) Benign — named case party → KEEP. Always carries a `Party: Plaintiff|Defendant` token:
```
Party: Plaintiff Nationstar Mortgage Llc        Party: Defendant Karagic, Muhamed
Party: Plaintiff The Bank Of New York Mellon     Party: Defendant Reid, Sharone V
Party: Defendant Merritt, Randolph               Party: Defendant Fedele, Michael ...
```

### C) Benign — counsel → KEEP. Carries `ESQ` / `CO-COUNSEL` / `AS COUNSEL FOR` / `DESIGNATION OF EMAIL`:
```
JUAN C MARTINEZ AS COUNSEL FOR CANBY BUSINESS PARK, LLC (PURCHASER)
CO-COUNSEL FOR POLIAKOFF BACKER, LLP
ANTHONY J. ALONEFTIS, ESQ.
```

### THE GENUINELY HARD CASES — bare firm/person name, no `Party:` token
A heuristic of "bare name with no `Party:` prefix ⇒ surplus firm" **misfires** on:
```
RANDOLPH MERRITT          ← the HOMEOWNER himself (cf. "Party: Defendant Merritt, Randolph" same case). FALSE KILL.
NASINNYA LLC / Non-Party Nisinnya, LLC   ← the auction HIGHEST BIDDER / purchaser, not a recovery firm.
MURTADHA PROPERTIES LLC   ← ambiguous buyer/investor.
MANOR GROVE VILLAGE ONE, INC. / TABERNACLE CHRISTIAN CENTER MINISTRIES, INC.  ← HOA / party orgs.
Tepps Treco               ← bare name, unknown.
```

**Conclusion for the classifier (your decision):** the `Party:`-token test cleanly
separates B+C (benign) from everyone else, but the residual "bare name" bucket is a
mix of surplus firms, the homeowner, the purchaser, and HOAs. Splitting *those*
needs more than the Party token:
1. **Known-company keyword list** (SURPLUS/FUNDING/RECOVERY/ASSIGNEE/TRUSTEE/
   PROCESSING/CONSULTATION/EQUITY) — catches the self-identifying firms above, AND
2. **Cross-reference the case's own party names** to exclude the homeowner appearing
   pro se (RANDOLPH MERRITT) and the named purchaser/HOA from the flag.

A pure heuristic without (2) WILL false-kill a homeowner. A pure keyword list
without (1)'s breadth will miss firms with neutral names (Capital Crafter, Asmart
Group). Recommend: **Party-token benign-filter → keyword flag → party-name exclusion.**

---

## Cases with CONFIRMED surplus-claim chains (these would be KILLED)
```
CACE-24-008631  Motion to Intervene (New Beginnings Trustee) → Order Granting "MOTION TO INTERVENE, CLAIM TO SURPLUS FUNDS ... DISBURSE SURPLUS"  + NOA AMERIFUND EQUITY GROUP
CACE-25-005168  Motion to Intervene (Andres Gentles, assignee) → Order Disbursing surplus  + NOA GET LIQUID FUNDING
CACE-24-012541  Motion for Disbursement (surplus proceeds) + assignee Motion to Intervene → Order Disbursing Funds (Get Liquid Funding) + NOA GET LIQUID FUNDING
CACE-23-015282  Motion to Intervene (Prestige Processing) → Agreed Order disbursing REMAINING SURPLUS FUNDS
CACE-25-002358  "MOTION TO DISBURSE SURPLUS FUNDS" + NOA assignee Capital Crafter → Order Disbursing Funds
CONO-25-048381  Motion to Intervene (Home Defense) → Order on Motion for Disbursement of Surplus Proceeds → Order Disbursing (surplus to Plaintiff HOA)
CACE-25-004451  Emergency Motion "To Claim For The Surplus Retained By Clerk Of $5,873.68 (3rd Party Bidder Romel Hilaire)"
COCE-25-085528  NOA PRIORITY SURPLUS LLC (firm circling; no disbursement order yet)
CACE-24-007420  NOA + Motion to Intervene The Recovery Agents, LLC
```

## False friends to ignore
- `Value Claim Form` — appears at case-FILING time (e.g. 2014, 2023), not post-sale. Contains "claim" but is unrelated to surplus. Do not match.
- `Joint Stipulation` — consent final judgment, not surplus.

---

## Throwaway-tooling reminder
`scripts/broward_investigate.py` and `.github/workflows/broward-investigate.yml`
remain **investigation-only** — not registered, not in the daily pipeline.
**Flag for removal before any production `core/dockets/broward.py` lands.**
