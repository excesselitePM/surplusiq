"""
SurplusIQ — Broward County Docket Scraper

Built 2026-06-10 from the investigation ground-truth in
`data/samples/broward/SURPLUS_VOCAB_FINDINGS.md` (Actions run 27312374787,
22 real cases, 9 with live surplus-claim activity). Investigation tooling
(scripts/broward_investigate.py + the Broward Investigate workflow) is REMOVED
as part of this commit — this file is the production replacement.

ACCESS PATH (proven headless from the GitHub Actions datacenter IP, zero CAPTCHA
— unlike Miami-Dade's v3 form, the Broward public path is a plain server-rendered
grid):

  1. GET  /Web2/CaseSearchECA/Index/?AccessLevel=ANONYMOUS         (session)
  2. GET  /Web2/CaseSearchECA/CaseNumberSearchResultsPUBLIC?CaseNum=<NODASH>
  3. CLICK button.bc-casedetail-viewer  (onclick=ViewDetails(`<~152-char Viewer
     blob>`) — the SHORT CaseID token is NOT the detail token; passing it 302s to
     an ASP.NET error. Click the rendered button = faithful user action.)
  4. → /Web2/CaseSearchECA/GetCaseDetail?Viewer=<blob>  (static HTML tables)
  5. Docket events table headers: Date | Description | Additional Text | View/Pages

DETECTION — THE KEY FINDING: the kill terms are NOT in the Description column.
`Description` holds GENERIC clerk labels (Motion for Disbursement, Motion to
Intervene, Order Disbursing Funds, Order Granting, Notice of Appearance). ALL
the surplus-specific text lives in `Additional Text`. We therefore scan the
COMBINED Description + Additional Text of every row, not Description alone.
A Description-only matcher misses every one of the 9 real claims.

See SURPLUS_VOCAB_FINDINGS.md for the per-string evidence cited inline below.
"""

from __future__ import annotations
import re
import html as _html
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent


BASE_URL = "https://www.browardclerk.org/Web2/CaseSearchECA"
LANDING_URL = f"{BASE_URL}/Index/?AccessLevel=ANONYMOUS"
REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

FORECLOSURE_MORTGAGE = "mortgage_foreclosure"


# ─────────────────────────────────────────────────────────────────────────────
# SURPLUS-CLAIM detection — match against COMBINED Description + Additional Text,
# case-insensitive, OCR-typo-tolerant. Every hit REQUIRES the "surplus" anchor so
# routine rows ("Certificate of Disbursements", "Value Claim Form", default
# motions naming "claimants") never false-fire. Real OCR corruption seen in the
# dockets — "ASS␣GNEE", "INTERVEN", "D␣SBURSE" — is matched on the distinctive
# stem, not the exact spelling.
#
# A row is a surplus claim when it has "surplus" AND a disbursement/claim verb.
# Verbs (stems, OCR tolerant):
SURPLUS_VERB_PATTERNS = [
    r"d\s?i?\s?sburs",      # disburse / disbursement / "D SBURSE" (CACE-25-002358 "MOTION TO DISBURSE SURPLUS FUNDS")
    r"claim",               # "CLAIM TO SURPLUS FUNDS" (CACE-24-008631); "Claim For The Surplus Retained By Clerk" (CACE-25-004451)
    r"proceed",             # "SURPLUS PROCEEDS" (CACE-24-012541; CONO-25-048381)
    r"interven",            # "MOTION TO INTERVENE AND TO DISBURSE SURPLUS FUNDS" (CACE-25-005168 / CACE-25-001564)
]
# Strong standalone phrases (surplus + a noun that only appears in claim filings):
SURPLUS_PHRASE_PATTERNS = [
    r"surplus\s+funds?",        # CACE-25-001564 "DISBURSEMENT OF SURPLUS FUNDS"
    r"surplus\s+proceeds",      # CACE-24-012541 "SURPLUS PROCEEDS"
    r"surplus\s+retained",      # CACE-25-004451 "Surplus Retained By Clerk"
    r"remaining\s+surplus",     # CACE-23-015282 "ORDER DISBURSING THE REMAINING SURPLUS FUNDS"
]

# ⚠️ SALE-ISSUE / BANKRUPTCY DETECTION DELIBERATELY NOT IMPLEMENTED HERE.
# Eric Rule 2 (sale vacated = hard kill) and Rule 1 (bankruptcy = flag) sound
# simple but are LOSSY on Broward's real dockets: a foreclosure sale routinely
# gets cancelled / postponed / "entered in error" / stayed for an earlier
# bankruptcy, then re-noticed and SOLD. Naive "Foreclosure Sale CANCELED" /
# "Motion to Cancel Sale" / "Motion to Vacate" matching false-killed 4 of 22 real
# sold-with-surplus cases (CACE-19-010632, CACE-21-019437, CACE-23-019364,
# CACE-24-007807) — every one has a Certificate of Sale + Certificate of Title
# dated AFTER the cancellations, and CACE-24-007807's last cancel motion was
# DENIED. We only ever scrape cases the auction step already marked SOLD, so the
# sale completed. A genuine post-title vacate kill needs date-ordered logic
# (vacate AFTER the Certificate of Title) — a separate scoped task, NOT this one.

# ⚠️ DELIBERATELY NOT A KILL TERM: "Certificate of Disbursement(s)".
# Eric's spec lists it as a surplus signal, but the ground-truth (22 cases) shows
# it on ~EVERY sold case — it's the clerk's routine record of paying sale proceeds
# to lienholders per the final judgment, NOT proof the SURPLUS was claimed. Killing
# on it would kill every lead. Flagged here as a documented contradiction with
# Eric's written spec; confirm with him before ever promoting it to a signal.
_ADMIN_NOISE_NOTE = "certificate of disbursements = routine admin, not a surplus claim"

# ─────────────────────────────────────────────────────────────────────────────
# NOTICE-OF-APPEARANCE classifier. Description is always the generic "Notice of
# Appearance"; the distinguishing data is entirely in Additional Text. Three-stage
# filter proven on the real data (SURPLUS_VOCAB_FINDINGS.md "hard classifier"):
#   Stage 1  benign-party  — carries a "Party: Plaintiff|Defendant" token
#   Stage 2  benign-counsel — ESQ / "as counsel for" / co-counsel / email designation
#   Stage 3  residual       — surplus-keyword / known-firm → KILL; else → CAUTION,
#                             with PARTY-NAME, purchaser/non-party, and HOA exclusions
# Stages 1+2 are gated by the KNOWN-FIRM guard ONLY (a ground-truthed recovery-firm
# NAME), NOT by generic keywords: a row that ALSO names a known recovery firm is NOT
# benigned (e.g. CONO-25-048381 "HOME DEFENSE ... EVO RECOVERY ... Party: Defendant
# Zoubaa" carries a Party token but names a known surplus firm). A generic keyword
# alone ("funding", "consulting", "equity group") must NOT override a structured
# party/counsel token — that was the GHIDOTTI-class false kill (a "Party: Plaintiff
# XYZ Funding LLC" is the case's own lender). Generic keywords act ONLY in Stage 3.

# Generic keywords that self-identify a surplus-recovery / funding operation:
RECOVERY_KEYWORDS = [
    "surplus",            # PRIORITY SURPLUS LLC (COCE-25-085528)
    "funding",            # GET LIQUID FUNDING, LLC (CACE-24-012541, CACE-25-005168, COWE-25-085495, COWE-26-005819)
    "recovery",           # The Recovery Agents, LLC (CACE-24-007420); EVO RECOVERY CONSULTATION (CONO-25-048381)
    # NOTE: bare "assignee" is intentionally NOT a keyword. It is ambiguous — a
    # PLAINTIFF can be a loan assignee, so "Attorneys for the Plaintiffs Assignee"
    # (GHIDOTTI/BERGER LLP, CACE-13-021361) is benign plaintiff counsel, not a
    # surplus firm. The live CI run false-killed that $327K lead on it. The real
    # owner-assignment recovery cases are caught by KNOWN_RECOVERY_FIRMS (New
    # Beginnings Trustee, Capital Crafter) and the surplus-claim motion path
    # ("Assignee's Motion to Disburse Surplus" → surplus+disburse). An unknown
    # "X as Assignee for <owner>" NOA falls to residual CAUTION, which is correct.
    "processing services",  # PRESTIGE PROCESSING SERVICES, LLC (CACE-23-015282)
    "consultation",       # EVO RECOVERY CONSULTATION CORP
    "consulting",
    "equity group",       # AMERIFUND EQUITY GROUP (CACE-24-008631)
]
# Specific firm names ground-truthed in the 9 claim cases — KILL even when the
# name carries no generic keyword (Eric's "known company list", seeded). Extend as
# Eric supplies more names.
KNOWN_RECOVERY_FIRMS = [
    "get liquid funding", "priority surplus", "the recovery agents", "amerifund",
    "new beginnings trustee", "new beginnings", "prestige processing",
    "home defense", "evo recovery", "capital crafter",
]
# Org keywords that mark a benign HOA / condo / church party (not a surplus firm):
HOA_ORG_KEYWORDS = [
    "homeowners association", "homeowner's association", "condominium", "condo association",
    "village", "ministries", "church", "property owners association", " hoa",
]
# Purchaser / auction-winner markers — the highest bidder is not a surplus claimant:
PURCHASER_MARKERS = ["non-party", "non party", "purchaser", "highest bidder", "successful bidder"]

# NOA verdicts
NOA_BENIGN = "benign"
NOA_RECOVERY_KILL = "recovery_firm"
NOA_RESIDUAL_CAUTION = "residual_caution"


def parse_broward_case_number(raw: str) -> Optional[dict]:
    """Parse a Broward case number → search components.

    Broward civil case format: 4-letter division prefix + 2-digit year + 6-digit
    sequence, e.g. CACE-25-003189, COCE-25-085528, COWE-26-005819, CONO-25-048381.
    The public search takes the de-dashed token (CaseNum=CACE25003189).

    Returns {prefix, year, number, nodash, dashed} or None.
    """
    if not raw:
        return None
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())          # strip " (12345)" auction suffix
    compact = re.sub(r"[^A-Za-z0-9]", "", cleaned).upper()
    m = re.match(r"^([A-Z]{4})(\d{2})(\d{6})$", compact)
    if not m:
        return None
    return {
        "prefix": m.group(1),
        "year":   m.group(2),
        "number": m.group(3),
        "nodash": compact,
        "dashed": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
    }


# ── pure helpers (network-free → unit-testable against committed docket JSON) ──

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def _name_tokens(name: str) -> frozenset:
    """Alpha tokens (len≥2) of a name, minus org-suffix noise. Used for the
    party-name exclusion so 'RANDOLPH MERRITT' matches 'Merritt, Randolph'."""
    toks = re.findall(r"[A-Za-z]{2,}", (name or "").lower())
    stop = {"llc", "inc", "corp", "co", "the", "and", "of", "as", "for", "party",
            "plaintiff", "defendant", "esq", "trust", "na", "association"}
    return frozenset(t for t in toks if t not in stop)


def collect_party_and_purchaser_names(rows: list) -> tuple:
    """Walk every row's Additional Text and collect:
      - party_names:     names following a 'Party: Plaintiff|Defendant <name>' or a
                         leading 'Defendant|Plaintiff <name>' token (the case's own
                         parties — used to exclude a pro-se homeowner appearance).
      - purchaser_names: names following a 'Non-Party <name>' token.
    Returns (set[frozenset_tokens], set[frozenset_tokens])."""
    party, purchaser = set(), set()
    for r in rows:
        ad = _norm(r.get("additional", ""))
        for m in re.finditer(r"party:\s*(?:plaintiff|defendant)\s+([^,]+(?:,\s*[A-Za-z.]+)?)", ad, re.I):
            toks = _name_tokens(m.group(1))
            if toks:
                party.add(toks)
        # leading "Defendant <name>" / "Plaintiff <name>" without the "Party:" prefix
        m2 = re.match(r"(?:defendant|plaintiff)\s+([A-Z][^,]{2,40})", ad, re.I)
        if m2:
            toks = _name_tokens(m2.group(1))
            if toks:
                party.add(toks)
        for m in re.finditer(r"non-?party\s+([A-Za-z0-9 .,&'-]{3,40})", ad, re.I):
            toks = _name_tokens(m.group(1))
            if toks:
                purchaser.add(toks)
    return party, purchaser


# ── Owner (defendant-homeowner) extraction ──────────────────────────────────
# Broward stores parties as lowercase token sets (for matching); for the OWNER we
# need the PROPER-CASED defendant name string. The docket text lists individuals
# as "Defendant Last, First M" — the comma-after-surname structure cleanly admits
# people and rejects multi-word corporates ("...Ministries Inc", "...Mortgage Llc")
# which lack it. Joint owners concatenate ("Fedele, Michael Defendant Fedele,
# Rae A.") → the pattern stops at the next role token, taking the first.
# Trailing middle-initial(s) must be a STANDALONE letter (negative lookahead for a
# following letter) — otherwise "Michael Defendant ..." captures a spurious "D"
# from the next role token.
_BROWARD_DEFENDANT_RE = re.compile(
    r"\bdefendant\s+([A-Za-z][A-Za-z'’.\-]+,\s+[A-Za-z][A-Za-z'’.\-]+"
    r"(?:\s+[A-Za-z](?![A-Za-z])\.?){0,2})",
    re.I)
_OWNER_CORP_MARKER = re.compile(
    r"\b(bank|mortgage|trust|funding|servicing|n\.?\s?a\.?|association|assn|"
    r"condominium|condo|homeowners?|l\.?l\.?c|llc|inc\b|incorporated|corp|company|"
    r"\bco\.|ltd|l\.?p\.?|holdings?|capital|\bfund\b|funds\b|department|united states|"
    r"ministries|church|realty|properties|\bgroup\b|investments?|enterprises?|"
    r"management|financial|lending|loans?)\b", re.I)
_OWNER_GENERIC = re.compile(
    r"unknow|tenant|any and all|all other|in possession|et al|john doe|jane doe|"
    r"\bdoe\b|parties claiming|lienors|creditors|\bheirs?\b|devisees", re.I)


def collect_defendant_names(rows: list) -> list:
    """Proper-cased 'Last, First' defendant names from the docket, in first-seen
    order, de-duplicated."""
    names, seen = [], set()
    for r in rows:
        ad = _norm(r.get("additional", ""))
        for m in _BROWARD_DEFENDANT_RE.finditer(ad):
            nm = m.group(1).strip().rstrip(",").strip()
            key = nm.lower()
            if key and key not in seen:
                seen.add(key)
                names.append(nm)
    return names


def owner_from_defendants(names: list) -> str:
    """First INDIVIDUAL defendant homeowner — excludes corporate/HOA/generic and
    any surplus-recovery firm. Bias to BLANK over a wrong (e.g. bank) name."""
    for nm in names:
        low = nm.lower()
        if _OWNER_CORP_MARKER.search(nm) or _OWNER_GENERIC.search(nm):
            continue
        if "surplus" in low or any(f in low for f in KNOWN_RECOVERY_FIRMS):
            continue
        return nm
    return ""


def _is_known_firm(text: str) -> bool:
    """Specific, ground-truthed recovery-firm NAMES only (Eric's known-company
    list). Distinct from _is_recovery_firm — it does NOT match generic keywords.
    This is the guard that disables the benign party/counsel stages: only a known
    firm name overrides a structured 'Party:'/counsel token, never a bare keyword."""
    low = text.lower()
    return any(k in low for k in KNOWN_RECOVERY_FIRMS)


def _is_recovery_firm(text: str) -> bool:
    low = text.lower()
    return (any(k in low for k in KNOWN_RECOVERY_FIRMS)
            or any(k in low for k in RECOVERY_KEYWORDS))


def classify_appearance(additional: str, party_names: set, purchaser_names: set) -> tuple:
    """Classify ONE Notice-of-Appearance row's Additional Text.

    Returns (verdict, firm_name) where verdict ∈ {benign, recovery_firm,
    residual_caution}. firm_name is the appearing-entity text for the reason line.
    """
    ad = _norm(additional)
    low = ad.lower()
    is_known = _is_known_firm(ad)
    is_recovery = _is_recovery_firm(ad)

    # Stage 1 — benign party token. Gated on a KNOWN-FIRM match ONLY, NOT generic
    # keywords: a "Party: Plaintiff XYZ Funding LLC" is the case's own lender,
    # benign — the generic "funding"/"consulting"/"equity group" keyword must not
    # override a structured party token (the GHIDOTTI-class false kill). A row
    # naming a ground-truthed recovery firm still falls through to the Stage-3 kill.
    if re.search(r"party:\s*(?:plaintiff|defendant)", low) and not is_known:
        return (NOA_BENIGN, ad)
    # leading "Defendant <name>" / "Plaintiff <name>" (no "Party:" prefix), e.g.
    # "Defendant Asmart Group, LLC" — a named case party, benign.
    if re.match(r"(?:defendant|plaintiff)\s+[A-Z]", ad, re.I) and not is_known:
        return (NOA_BENIGN, ad)

    # Stage 2 — benign counsel. Same known-firm gate. "Attorneys for the
    # Plaintiff(s)/Defendant(s)" (incl. "...for Plaintiff XYZ Equity Group") is case
    # counsel, benign — what the GHIDOTTI/BERGER live false kill needed.
    if not is_known and re.search(
            r"\besq\b|as counsel for|co-?counsel|attorneys? for|"
            r"designation of (?:electronic )?(?:e-?)?mail", low):
        return (NOA_BENIGN, ad)

    # Stage 3 — residual bucket. Apply exclusions BEFORE flagging.
    appear_toks = _name_tokens(ad)
    # (a) the appearance is the case's own party (e.g. pro-se homeowner Randolph
    #     Merritt, who is also "Party: Defendant Merritt, Randolph") → benign.
    if appear_toks and any(len(appear_toks & p) >= 2 for p in party_names):
        return (NOA_BENIGN, ad)
    # (b) purchaser / auction winner / non-party → benign.
    if any(mk in low for mk in PURCHASER_MARKERS):
        return (NOA_BENIGN, ad)
    if appear_toks and any(len(appear_toks & p) >= 2 for p in purchaser_names):
        return (NOA_BENIGN, ad)
    # (c) HOA / condo / church / association → benign.
    if any(k in low for k in HOA_ORG_KEYWORDS):
        return (NOA_BENIGN, ad)

    # Residual firm. Known/keyword recovery firm → KILL. Unknown → CAUTION.
    if is_recovery:
        return (NOA_RECOVERY_KILL, ad)
    return (NOA_RESIDUAL_CAUTION, ad)


def _row_text(r: dict) -> str:
    return _norm(f"{r.get('description','')} {r.get('additional','')}")


def detect_surplus_claim(rows: list) -> str:
    """Return the verbatim Additional/Description text of the first surplus-claim
    row, or '' if none. A row qualifies when it carries the 'surplus' anchor AND a
    disbursement/claim verb (or a strong surplus phrase)."""
    for r in rows:
        blob = _row_text(r)
        low = blob.lower()
        if "surplus" not in low:
            continue
        if any(re.search(p, low) for p in SURPLUS_VERB_PATTERNS) or \
           any(re.search(p, low) for p in SURPLUS_PHRASE_PATTERNS):
            return blob
    return ""


class BrowardDocketScraper(DocketScraper):

    county_id = "broward-fl"
    county_name = "Broward"

    def classify(self, result: DocketResult, final_sale_price: float) -> tuple:
        """No-op override (same rationale as Miami-Dade): scrape_case already ran
        the full Broward evidence model. The base prayer-vs-sale math is wrong for
        FL (opening_bid IS the debt; no prayer field), so we return the
        docket-computed classification unchanged rather than let enrich.run_one
        re-derive it."""
        return (result.classification, result.classification_reason)

    # ── Phase 2: parse extracted docket rows → Eric's review fields ──────────
    # Takes ROWS (not raw HTML) so it is fully network-free and unit-testable
    # against data/samples/broward/ci/<CASE>_docket.json.

    def parse_docket(self, rows: list, result: DocketResult,
                     case_caption: str = "") -> None:
        rows = rows or []
        result.events = [
            DocketEvent(filing_date=r.get("date", ""),
                        description=(_norm(r.get("description", ""))[:200]),
                        document_type=(_norm(r.get("additional", ""))[:200])).__dict__
            for r in rows[:120]
        ]

        party_names, purchaser_names = collect_party_and_purchaser_names(rows)
        # Owner-completeness signal: did we recover at least one case party name?
        result.defendants = [" ".join(sorted(p)) for p in party_names][:20]
        # Owner = proper-cased individual defendant homeowner (NOT the token sets
        # above, NOT plaintiff/corporate/HOA/purchaser/surplus-firm).
        result.owner_name = owner_from_defendants(collect_defendant_names(rows))

        # Sale confirmation (for the "Certificate of Sale found" valid-reason line).
        sale_confirmed = any(
            re.search(r"certificate of (?:sale|title)", _row_text(r), re.I) for r in rows
        )

        # ── Surplus-claim activity (PRIMARY kill path) ──
        claim_evidence = detect_surplus_claim(rows)

        # ── Notice-of-Appearance classifier (SECONDARY kill path) ──
        noa_kill_firm = ""
        noa_caution_firm = ""
        for r in rows:
            if "notice of appearance" not in (r.get("description", "") or "").lower():
                continue
            verdict, firm = classify_appearance(r.get("additional", ""),
                                                party_names, purchaser_names)
            if verdict == NOA_RECOVERY_KILL and not noa_kill_firm:
                noa_kill_firm = firm
            elif verdict == NOA_RESIDUAL_CAUTION and not noa_caution_firm:
                noa_caution_firm = firm

        # Stash for _apply_evidence_level (auditable evidence strings).
        result._evidence = {                       # type: ignore[attr-defined]
            "claim": claim_evidence,
            "noa_kill": noa_kill_firm,
            "noa_caution": noa_caution_firm,
            "sale_confirmed": sale_confirmed,
            "owner_present": bool(party_names),
            "docket_rows": len(rows),
        }

    def _apply_evidence_level(self, result: DocketResult) -> None:
        """Map docket evidence → evidence_level / lead_status / classification +
        plain-language reason (Eric's templates). Precedence, most-disqualifying
        first: surplus claim → recovery NOA → residual unknown NOA(caution) →
        clean (Tier A pursuable / Tier B-C caution).

        Sale-issue / bankruptcy are intentionally NOT decided here — see the note
        at the top of the module (lossy on Broward's cancel-and-resell dockets).
        """
        result.foreclosure_type = FORECLOSURE_MORTGAGE
        ev = getattr(result, "_evidence", {})

        # 1 — surplus-claim activity already filed → KILLED.
        if ev.get("claim"):
            result.claim_filed = True
            result.claim_type = ev["claim"][:200]
            result.kill_signals = ["surplus_claim_filed"]
            result.evidence_level = "claim_filed"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = (
                f"Docket checked. Surplus claim activity found: '{ev['claim'][:160]}'. "
                f"Lead already being pursued."
            )
            return

        # 2 — surplus-recovery firm filed a Notice of Appearance → KILLED.
        if ev.get("noa_kill"):
            result.claim_filed = True
            result.claim_type = f"Notice of Appearance: {ev['noa_kill']}"
            result.kill_signals = ["surplus_firm_appearance"]
            result.evidence_level = "claim_filed"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = (
                f"Docket checked. Notice of Appearance found from {ev['noa_kill']}. "
                f"Lead already being pursued."
            )
            return

        # 3 — residual NOA from an UNKNOWN firm → CAUTION (per approved decision:
        #     unknown residual firm = possible surplus claimant, manual review).
        if ev.get("noa_caution"):
            result.evidence_level = "pursuable_with_caution"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                f"Docket checked. Possible surplus claimant appeared: {ev['noa_caution']}. "
                f"Not on known-recovery-firm list — manual review required."
            )
            return

        # 4 — clean docket. Tier A (pursuable) vs Tier B/C (caution) by completeness.
        sale_line = ("Certificate of Sale found. " if ev.get("sale_confirmed")
                     else "Sale confirmed on auction side. ")
        if ev.get("owner_present") and ev.get("docket_rows", 0) > 0:
            result.evidence_level = "no_claim_found"
            result.lead_status = "pursuable"
            result.classification = "green"
            result.classification_reason = (
                f"Docket checked. {sale_line}No notice of appearance, surplus claim, "
                f"or motion for surplus funds found."
            )
        else:
            # Tier B/C — owner/party not recoverable from the docket, or docket
            # sparse/weak. Address completeness is enforced downstream by the
            # loader (it holds the auction-side address); the scraper flags the
            # owner/docket gap here.
            result.evidence_level = "pursuable_with_caution"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                "Docket checked. No surplus claim found, but owner/address/unit "
                "incomplete or docket partial. Manual review required."
            )

    # ── Phase 1: drive the public path + detail click, return docket rows ────

    async def fetch_docket(self, case_number: str) -> dict:
        """Return {ok, url, rows, caption, case_present, error}. Rows are the
        Date|Description|Additional Text entries off the GetCaseDetail page.
        Never fabricates: on any failure rows=[] and ok=False."""
        parsed = parse_broward_case_number(case_number)
        if not parsed:
            return {"ok": False, "url": "", "rows": [], "caption": "",
                    "case_present": False,
                    "error": f"case number not parseable: {case_number}"}

        out = {"ok": False, "url": "", "rows": [], "caption": "",
               "case_present": False, "error": ""}
        diag = Path("data/diagnostics/broward-fl")
        diag.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1400, "height": 1000},
                ignore_https_errors=True, user_agent=REAL_UA,
            )
            page = await context.new_page()
            try:
                # 1 — landing (session)
                await page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=45000)

                # 2 — public search
                search_url = f"{BASE_URL}/CaseNumberSearchResultsPUBLIC?CaseNum={parsed['nodash']}"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                try:
                    await page.wait_for_selector('[onclick*="ViewDetails"], button.bc-casedetail-viewer',
                                                 timeout=15000)
                except PWTimeout:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except PWTimeout:
                    pass

                # 3 — extract the real Viewer token (NOT the #= placeholder, NOT CaseID)
                trig = await page.evaluate(
                    r"""() => {
                        const links = Array.from(document.querySelectorAll('[onclick*="ViewDetails"]'));
                        for (const a of links) {
                            const oc = a.getAttribute('onclick') || '';
                            const m = oc.match(/ViewDetails\(`([^`]+)`\)/);
                            if (m && m[1].indexOf('#=') === -1) return m[1];
                        }
                        return null;
                    }"""
                )
                if not trig:
                    out["error"] = "no rendered ViewDetails trigger on results page"
                    return out

                # 4 — click the real detail button → GetCaseDetail
                link = page.locator('button.bc-casedetail-viewer, [onclick*="ViewDetails"]').first
                try:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                        await link.click(timeout=10000)
                except Exception:
                    # Fallback: invoke ViewDetails with the extracted token directly.
                    try:
                        async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                            await page.evaluate("(t) => ViewDetails(t)", trig)
                    except Exception as e2:
                        out["error"] = f"detail navigation failed: {str(e2)[:140]}"
                        return out
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except PWTimeout:
                    pass

                url = page.url
                if "aspxerrorpath" in url.lower() or "/web2/error" in url.lower():
                    out["error"] = f"landed on ASP.NET error page ({url[:80]})"
                    out["url"] = url
                    return out

                # 5 — extract docket rows (Date | Description | Additional Text)
                rows = await page.evaluate(self._EXTRACT_DOCKET_JS)
                caption = await page.evaluate(
                    r"""() => {
                        const t = (document.body.innerText || '');
                        const m = t.match(/([A-Z][A-Za-z .,&'-]{4,90}\s+vs\.?\s+[A-Z][A-Za-z .,&'-]{3,90})/);
                        return m ? m[1] : '';
                    }"""
                )

                # anti-fabrication: the searched case number MUST appear on the page
                page_compact = re.sub(r"[^A-Za-z0-9]", "",
                                      (await page.content())).upper()
                case_present = parsed["nodash"] in page_compact

                out.update({"ok": bool(rows) and case_present, "url": url,
                            "rows": rows or [], "caption": _norm(caption),
                            "case_present": case_present})
                if not rows:
                    out["error"] = "detail page reached but docket table not found"
                elif not case_present:
                    out["error"] = (f"detail page does not contain searched case "
                                    f"{parsed['nodash']} — possible wrong/empty result")
                return out
            except PWTimeout as e:
                out["error"] = f"timeout: {str(e)[:140]}"
                return out
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {str(e)[:140]}"
                return out
            finally:
                await browser.close()

    # JS that parses the docket events table(s) — same logic proven in the probe.
    _EXTRACT_DOCKET_JS = r"""() => {
        const norm = s => (s||'').replace(/ /g,' ').replace(/\s+/g,' ').trim();
        const out = [];
        for (const t of Array.from(document.querySelectorAll('table'))) {
            const headRow = t.querySelector('thead tr') || t.rows[0];
            if (!headRow) continue;
            const cells = Array.from(headRow.cells).map(c => norm(c.innerText).toLowerCase());
            const de = cells.findIndex(h => h.includes('description'));
            const ad = cells.findIndex(h => h.includes('additional'));
            const di = cells.findIndex(h => h.includes('date'));
            if (de === -1 || ad === -1) continue;
            const body = t.querySelector('tbody')
                ? Array.from(t.querySelector('tbody').querySelectorAll('tr'))
                : Array.from(t.rows).slice(1);
            for (const r of body) {
                const c = Array.from(r.cells).map(x => norm(x.innerText));
                if (!c.length) continue;
                const desc = de >= 0 ? (c[de]||'') : '';
                const addl = ad >= 0 ? (c[ad]||'') : '';
                if (!desc && !addl) continue;
                out.push({ date: di >= 0 ? (c[di]||'') : '', description: desc, additional: addl });
            }
        }
        return out;
    }"""

    # ── scrape_case: full pipeline ───────────────────────────────────────────

    async def scrape_case(self, case_number: str) -> DocketResult:
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
            foreclosure_type=FORECLOSURE_MORTGAGE,
        )
        fetched = await self.fetch_docket(case_number)
        result.case_url = LANDING_URL          # stable verify link (no per-case URL)
        if not fetched["ok"]:
            result.classification = "unknown"
            result.evidence_level = "auction_only"
            result.classification_reason = f"docket retrieval failed: {fetched['error']}"
            return result
        if fetched.get("caption"):
            result.case_title = fetched["caption"][:200]
        self.parse_docket(fetched["rows"], result, case_caption=fetched.get("caption", ""))
        self._apply_evidence_level(result)
        return result
