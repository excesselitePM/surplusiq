"""
SurplusIQ — Duval County (Jacksonville) Docket Scraper

Built 2026-06-22 from the investigation ground-truth in
`data/samples/duval/FINDINGS.md` (GitHub Actions probe runs; 7 clean sold cases
+ 8 party-search claim cases, real text in data/samples/duval/{ci,party}/*.txt).

ACCESS PATH (proven headless from the Actions datacenter IP — Tyler "CORE" CMS,
ASP.NET WebForms + ASMX backend; reCAPTCHA v2 gates only the *registered* login,
NOT anonymous Public Access):

  1. GET CoreCms.aspx?mode=PublicAccess  → auto CoreWebSvc.asmx/PublicLogin
     establishes an anonymous "Public Access" session (no CAPTCHA). MUST wait for
     it to settle (#c_AccessTypeLabel == "Public Access") before searching, or the
     login/captcha dialog appears instead of the search form.
  2. openCmsPage()  → CoreWebSvc.asmx/GetNewSearchTab renders the search form
     (single frame, no iframe).
  3. paste the full uniform case number into c_UcnEntryBox_<GUID> →
     getCaseTabByUcnBoxId(boxId) → CoreWebSvc.asmx/GetCaseByUcn → the full
     case-detail tab renders inline. ONE page, NO pagination (docket "Line"
     numbers are sequential 1..N — max(Line) == entry count is the capture check).

DETECTION — three CROSS-CONFIRMING surplus-claim kill signals, all proven on real
Duval cases (see FINDINGS.md). The surplus-claim text lives in the docket
DESCRIPTION column (NOT a separate "Additional Text" column like Broward):

  1. PARTY-TYPE   — claimant registered as a case party of type "3rd Party" /
                    "THIRD PARTY DEFENDANT" whose name matches surplus vocab or the
                    Duval-local firm list.
  2. FEE-CODE     — Fees section carries SURPLUS-DISB PROCEEDS-EA /
                    SURPLUS-APPOINT TRUSTEE / SURPLUS-NOTIFY TRUSTEE APPT (these
                    appear ONLY when a surplus disbursement/trustee process ran).
  3. TEXT         — a Description with the "surplus" (or "excess proceeds") anchor
                    AND a disburse/claim/petition/intent/authorize verb.

SURPLUS AMOUNT = the court-registry balance after the plaintiff payoff, taken from
"CERTIFICATE OF (FORECLOSURE )?DISBURSEMENT(S) ... BALANCE/BAL $Y" — the real
number, stronger than opening-bid math.
"""

from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent


LANDING_URL = "https://core.duvalclerk.com/CoreCms.aspx?mode=PublicAccess"
REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

FORECLOSURE_MORTGAGE = "mortgage_foreclosure"


# ─────────────────────────────────────────────────────────────────────────────
# DUVAL-LOCAL recovery-firm list (Jacksonville). DISTINCT from Broward's set —
# the cross-county check (FINDINGS.md) showed Duval is dominated by these local
# firms; Broward firms mostly don't operate here (only Get Liquid Funding reaches
# Duval, rarely). Keep this list SEPARATE from Broward's KNOWN_RECOVERY_FIRMS.
DUVAL_RECOVERY_FIRMS = [
    "surplus refund corp", "surplus refund corporation",
    "surplus return group", "surplus recovery", "surplus funds recovery",
    "surplus funds usa", "national equity recovery", "get liquid funding",
]

# Signal 3 (TEXT): a docket Description is a surplus claim when it carries the
# "surplus" anchor AND a claim/disburse/petition/authorize verb — OR "excess
# proceeds" with a claim/intent verb. Every match REQUIRES the anchor so the
# routine plaintiff payoff cert ("CERTIFICATE OF FORECLOSURE DISBURSEMENT ... TO:
# <plaintiff> ... BALANCE") never fires (it has no "surplus" token), and the
# cover-sheet "VALUE OF REAL PROPERTY OR MORTGAGE FORECLOSURE CLAIM" is guarded.
_SURPLUS_VERB = re.compile(
    r"disburs|claim|petition|interven|authoriz|direct\s+clerk|"
    r"appoint\w*\s+(?:a\s+)?(?:surplus\s+)?trustee|appointment of surplus trustee",
    re.I,
)
_EXCESS_VERB = re.compile(r"claim|intent", re.I)
_COVER_SHEET_GUARD = re.compile(
    r"value of real property or mortgage foreclosure claim", re.I)

# Signal 2 (FEE-CODE): surplus-process fee codes seen ONLY on claimed cases.
_SURPLUS_FEE = re.compile(r"surplus\s*[-–]\s*(disb|appoint|notify)", re.I)

# NOA: "NOTICE OF APPEARANCE OF COUNSEL <attorney> FOR <party>" — the represented
# party is named inline; a recovery firm in the FOR-clause is a kill.
_NOA_RE = re.compile(
    r"notice of appearance of counsel\s+(.+?)\s+for\s+(.+?)\s*$", re.I)

# Bankruptcy — FLAG, never kill (Eric Rule 1). Duval vocabulary.
_BANKRUPTCY_RE = re.compile(r"suggestion of bankruptcy|notice of bankruptcy", re.I)

# Sale issue — kill ONLY if a vacate/set-aside (not denied) is dated AFTER the
# Certificate of Title. Pre-title cancel/reschedule churn is NOISE (cases sell
# after several cancellations — proven on 005932 & 004483).
_TITLE_RE = re.compile(r"certificate of title", re.I)
_VACATE_RE = re.compile(r"vacat\w*|set aside", re.I)
_VACATE_DENY_GUARD = re.compile(r"deny|denied|denying|withdraw|withdrawn|moot", re.I)

# Surplus amount: balance after the plaintiff payoff.
_DISBURSE_CERT_RE = re.compile(r"certificate of (?:foreclosure )?disbursement", re.I)
_BALANCE_RE = re.compile(r"bal(?:ance)?\.?\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)", re.I)
_ADDITIONAL_ADVANCES_RE = re.compile(
    r"motion.{0,40}additional advances from the registry", re.I)

# Party-type tokens that mark a real case party.
_PARTY_TYPE_RE = re.compile(
    r"^(plaintiff|defendant|3rd party|third party.*|petitioner|respondent|"
    r"guardian.*|trustee|intervenor|other.*)$", re.I)
_THIRD_PARTY_RE = re.compile(r"3rd party|third party", re.I)


def parse_duval_case_number(raw: str) -> Optional[dict]:
    """Parse a Duval case number → {year, division, seq, entry, dashed}.

    Auction feed format: 16-YYYY-CA-NNNNNN-AXXX-MA (e.g. 16-2025-CA-005932-AXXX-MA).
    The county prefix '16-' and trailing '-MA' are optional. Tax-deed (NNNN-TD)
    numbers are a SEPARATE portal and return None here.
    """
    if not raw:
        return None
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())          # strip auction suffix
    if re.search(r"\d{3,4}\s*TD\b", cleaned, re.I):                 # tax deed — not this portal
        return None
    m = re.search(r"(?:16-)?(\d{4})-(C[A-Z])-(\d{6})", cleaned, re.I)
    if not m:
        return None
    year, div, seq = m.group(1), m.group(2).upper(), m.group(3)
    return {
        "year": year, "division": div, "seq": seq,
        # entry = the full UCN the c_UcnEntryBox accepts (proven in the probe)
        "entry": cleaned if cleaned.startswith("16-") else f"16-{year}-{div}-{seq}-AXXX-MA",
        "dashed": f"16-{year}-{div}-{seq}",
    }


# ── pure helpers (network-free → unit-testable against committed case text) ──

def _norm(s: str) -> str:
    return re.sub(r"[ \t]+", " ", (s or "").replace(" ", " ")).strip()


def _pdate(s: str) -> Optional[datetime]:
    for fmt in ("%m/%d/%Y", "%-m/%-d/%Y"):
        try:
            return datetime.strptime(s.strip(), "%m/%d/%Y")
        except ValueError:
            continue
    return None


def _section(lines: list, name: str, enders: tuple) -> list:
    """Return the lines strictly between a standalone `name` header and the next
    standalone header in `enders` (or end of text)."""
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == name:
            start = i + 1
            break
    if start is None:
        return []
    out = []
    for ln in lines[start:]:
        if ln.strip() in enders:
            break
        out.append(ln)
    return out


def parse_case_text(text: str) -> dict:
    """Parse the case-detail tab innerText into structured sections. Used by BOTH
    the live scraper (on the captured frame text) and the acceptance test (on the
    committed sample .txt) — identical parsing, so the test proves the real path.

    Returns {case_number, status, division, parties:[(name,type)], fees:[desc],
    dockets:[(date,description)], line_max, capture_ok}.
    """
    text = (text or "").replace(" ", " ")
    lines = text.split("\n")

    case_number = ""
    m = re.search(r"Case\s+(16-\d{4}-C[A-Z]-\d{6}-[A-Z0-9]+-[A-Z]{2})", text)
    if m:
        case_number = m.group(1)
    status = ""
    m = re.search(r"Case Status\t([^\t]+)", text)
    if m:
        status = m.group(1).strip()
    division = ""
    m = re.search(r"Division\t([A-Za-z0-9-]+)", text)
    if m:
        division = m.group(1).strip()

    # Parties: "NAME\tPARTY_TYPE"
    parties = []
    for ln in _section(lines, "Parties", ("Attorneys", "Fees", "Court Events", "Dockets")):
        if "\t" not in ln:
            continue
        name, _, rest = ln.partition("\t")
        rest = rest.strip()
        if _PARTY_TYPE_RE.match(rest) and name.strip():
            parties.append((name.strip(), rest))

    # Fees: "DATE\tDESCRIPTION\t$..."
    fees = []
    for ln in _section(lines, "Fees", ("Court Events", "Dockets")):
        m = re.match(r"\s*\d{1,2}/\d{1,2}/\d{4}\t([^\t]+)\t\$", ln)
        if m:
            fees.append(m.group(1).strip())

    # Dockets: entered-date+description line "DATE\tDESCRIPTION\t..."; the Line
    # numbers (bare ints / leading "N\t--") give the completeness check.
    dockets = []
    line_nums = []
    dk = _section(lines, "Dockets", ("User Name:", "Login Status:"))
    for ln in dk:
        s = ln.strip()
        mln = re.match(r"^(\d{1,3})(?:\t--|\s*$)", ln)
        if mln:
            line_nums.append(int(mln.group(1)))
        m = re.match(r"^(\d{1,2}/\d{1,2}/\d{4})\t(?!--)([^\t]{3,})", ln)
        if m:
            dockets.append((m.group(1), _norm(m.group(2))))

    line_max = max(line_nums) if line_nums else 0
    # capture check: sequential Line numbers, and the count of entries ≈ line_max
    capture_ok = bool(dockets) and (line_max == 0 or abs(line_max - len(dockets)) <= 2)

    return {
        "case_number": case_number, "status": status, "division": division,
        "parties": parties, "fees": fees, "dockets": dockets,
        "line_max": line_max, "capture_ok": capture_ok,
    }


def detect_surplus_party(parties: list) -> str:
    """Signal 1 — a 3rd-party claimant whose name is surplus/recovery vocab."""
    for name, ptype in parties:
        if _THIRD_PARTY_RE.search(ptype):
            low = name.lower()
            if "surplus" in low or any(f in low for f in DUVAL_RECOVERY_FIRMS):
                return f"{name} ({ptype})"
    return ""


def detect_surplus_fee(fees: list) -> str:
    """Signal 2 — a surplus-process fee code (claimed cases only)."""
    for desc in fees:
        if _SURPLUS_FEE.search(desc):
            return desc
    return ""


def detect_surplus_text(dockets: list) -> str:
    """Signal 3 — a Description with the surplus/excess anchor + a claim verb.
    Guards the cover-sheet and the routine plaintiff payoff cert."""
    for _date, desc in dockets:
        low = desc.lower()
        if _COVER_SHEET_GUARD.search(low):
            continue
        if "surplus" in low and _SURPLUS_VERB.search(low):
            return desc
        if "excess proceeds" in low and _EXCESS_VERB.search(low):
            return desc
    return ""


def classify_noa(dockets: list) -> tuple:
    """Find a recovery-firm Notice of Appearance. Returns (verdict, description,
    for_party) with verdict ∈ {recovery, benign, ''}. 'recovery' kills; a benign
    NOA (bank/homeowner/HOA counsel) does not."""
    for _date, desc in dockets:
        m = _NOA_RE.search(desc)
        if not m:
            continue
        for_party = m.group(2).strip()
        low = for_party.lower()
        if "surplus" in low or any(f in low for f in DUVAL_RECOVERY_FIRMS):
            return ("recovery", desc, for_party)
        return ("benign", desc, for_party)        # named a real party → benign
    return ("", "", "")


def extract_surplus_balance(dockets: list) -> float:
    """Surplus = the registry balance after the FIRST (plaintiff payoff)
    disbursement cert. Modern single-line 'BALANCE: $Y'; older sequences put the
    running '(BAL $Y)' on each line — the first one is the post-payoff surplus."""
    for _date, desc in dockets:
        if _DISBURSE_CERT_RE.search(desc):
            m = _BALANCE_RE.search(desc)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    return 0.0
    return 0.0


def detect_bankruptcy(dockets: list) -> str:
    for _date, desc in dockets:
        if _BANKRUPTCY_RE.search(desc):
            return desc
    return ""


def detect_sale_issue(dockets: list) -> str:
    """Kill only if a vacate/set-aside (not denied) is dated AFTER the Certificate
    of Title. Pre-title cancel/reschedule churn is noise."""
    title_date = None
    for date, desc in dockets:
        if _TITLE_RE.search(desc):
            d = _pdate(date)
            if d and (title_date is None or d > title_date):
                title_date = d
    if title_date is None:
        return ""
    for date, desc in dockets:
        if _VACATE_RE.search(desc) and not _VACATE_DENY_GUARD.search(desc):
            d = _pdate(date)
            if d and d > title_date:
                return desc
    return ""


class DuvalDocketScraper(DocketScraper):

    county_id = "duval-fl"
    county_name = "Duval"

    def classify(self, result: DocketResult, final_sale_price: float) -> tuple:
        """No-op override (same as Miami-Dade/Broward): scrape_case already ran the
        full Duval evidence model. The base prayer-vs-sale math is wrong for FL, so
        return the docket-computed classification unchanged."""
        return (result.classification, result.classification_reason)

    # ── Phase 2: parse case-detail text → Eric's review fields (network-free) ──

    def parse_docket(self, text: str, result: DocketResult) -> None:
        sec = parse_case_text(text)
        result.events = [
            DocketEvent(filing_date=d, description=desc[:200]).__dict__
            for d, desc in sec["dockets"][:160]
        ]
        result.defendants = [n for n, t in sec["parties"] if "defendant" in t.lower()][:20]

        claim_party = detect_surplus_party(sec["parties"])
        claim_fee = detect_surplus_fee(sec["fees"])
        claim_text = detect_surplus_text(sec["dockets"])
        noa_verdict, noa_desc, noa_for = classify_noa(sec["dockets"])
        surplus_balance = extract_surplus_balance(sec["dockets"])
        clawback = any(_ADDITIONAL_ADVANCES_RE.search(d) for _dt, d in sec["dockets"])
        bankruptcy = detect_bankruptcy(sec["dockets"])
        sale_issue = detect_sale_issue(sec["dockets"])
        title_present = any(_TITLE_RE.search(d) for _dt, d in sec["dockets"])
        owner_present = bool(result.defendants)

        if surplus_balance > 0:
            result.prayer_amount = surplus_balance      # FL: registry balance IS the surplus
            result.debt_source = "duval_disbursement_balance"

        result._evidence = {                            # type: ignore[attr-defined]
            "claim_party": claim_party,
            "claim_fee": claim_fee,
            "claim_text": claim_text,
            "noa_recovery": noa_desc if noa_verdict == "recovery" else "",
            "noa_for": noa_for,
            "surplus_balance": surplus_balance,
            "clawback_pending": clawback,
            "bankruptcy": bankruptcy,
            "sale_issue": sale_issue,
            "title_present": title_present,
            "owner_present": owner_present,
            "docket_rows": len(sec["dockets"]),
            "capture_ok": sec["capture_ok"],
            "case_number": sec["case_number"],
        }

    def _apply_evidence_level(self, result: DocketResult) -> None:
        """Map evidence → evidence_level / lead_status / classification + plain
        reason. Precedence (most-disqualifying first): surplus claim → sale issue
        (post-title vacate) → bankruptcy (FLAG) → residual caution → clean."""
        result.foreclosure_type = FORECLOSURE_MORTGAGE
        ev = getattr(result, "_evidence", {})

        # 1 — SURPLUS CLAIM (any of the three signals, or a recovery-firm NOA).
        signals = []
        if ev.get("claim_party"):
            signals.append(f"3rd-party claimant '{ev['claim_party']}'")
        if ev.get("claim_fee"):
            signals.append(f"surplus fee code '{ev['claim_fee']}'")
        if ev.get("claim_text"):
            signals.append(f"docket '{ev['claim_text'][:90]}'")
        if ev.get("noa_recovery"):
            signals.append(f"recovery-firm appearance '{ev['noa_recovery'][:90]}'")
        if signals:
            result.claim_filed = True
            result.claim_type = "; ".join(signals)[:300]
            result.kill_signals = ["surplus_claim_filed"]
            result.evidence_level = "claim_filed"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = (
                f"Docket checked. Surplus claim activity found: {'; '.join(signals)}. "
                f"Lead already being pursued."
            )
            return

        # 2 — SALE ISSUE: vacate/set-aside AFTER Certificate of Title (hard kill).
        if ev.get("sale_issue"):
            result.kill_signals = ["sale_vacated_post_title"]
            result.evidence_level = "sale_issue_found"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = (
                f"Docket checked. Sale vacated/set aside after Certificate of Title: "
                f"'{ev['sale_issue'][:120]}'. Hard kill."
            )
            return

        # 3 — BANKRUPTCY: flag, never kill (Eric Rule 1).
        if ev.get("bankruptcy"):
            result.evidence_level = "bankruptcy_found"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                f"Docket checked. Bankruptcy filed ('{ev['bankruptcy'][:90]}') — "
                f"flag for human review, not a kill."
            )
            return

        # 4 — RESIDUAL CAUTION: unknown surplus-claimant NOA, or a pending
        #     plaintiff registry-clawback that may reduce the surplus.
        if ev.get("noa_for") and not ev.get("noa_recovery") and "surplus" in ev["noa_for"].lower():
            result.evidence_level = "pursuable_with_caution"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                f"Docket checked. Possible surplus claimant appeared: {ev['noa_for']}. "
                f"Not on the known-firm list — manual review required."
            )
            return
        if ev.get("clawback_pending"):
            result.evidence_level = "pursuable_with_caution"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                "Docket checked. No surplus claim, but a plaintiff 'Motion for "
                "Additional Advances from the Registry' is pending — clawback may "
                "reduce the surplus. Manual review required."
            )
            return

        # 5 — CLEAN docket. Tier A (owner present) vs Tier B/C (incomplete).
        bal = ev.get("surplus_balance", 0)
        bal_line = (f"Registry surplus balance ${bal:,.2f}. " if bal > 0
                    else "Sale confirmed on auction side. ")
        if ev.get("owner_present") and ev.get("docket_rows", 0) > 0:
            result.evidence_level = "no_claim_found"
            result.lead_status = "pursuable"
            result.classification = "green"
            result.classification_reason = (
                f"Docket checked. {bal_line}No surplus claim, recovery-firm "
                f"appearance, or surplus-disbursement motion found."
            )
        else:
            result.evidence_level = "pursuable_with_caution"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                "Docket checked. No surplus claim found, but owner/address or docket "
                "incomplete. Manual review required."
            )

    # ── Phase 1: drive the Public-Access path, return case-detail text ──────────

    async def fetch_docket(self, case_number: str) -> dict:
        """Return {ok, url, text, case_present, error}. text = case-detail tab
        innerText. Never fabricates: on any failure text='' and ok=False."""
        parsed = parse_duval_case_number(case_number)
        if not parsed:
            return {"ok": False, "url": "", "text": "", "case_present": False,
                    "error": f"case number not parseable (or tax-deed): {case_number}"}

        out = {"ok": False, "url": "", "text": "", "case_present": False, "error": ""}
        diag = Path("data/diagnostics/duval-fl")
        diag.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1400, "height": 1400},
                ignore_https_errors=True, user_agent=REAL_UA,   # portal SSL cert is expired
            )
            page = await context.new_page()
            try:
                await page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=60000)
                # WAIT for anonymous PublicLogin to settle before opening search.
                try:
                    await page.wait_for_function(
                        "() => { const e=document.getElementById('c_AccessTypeLabel'); "
                        "return e && /public access/i.test(e.innerText); }", timeout=30000)
                except PWTimeout:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except PWTimeout:
                    pass

                await page.evaluate("() => { if (typeof openCmsPage==='function') openCmsPage(); }")
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except PWTimeout:
                    pass
                try:
                    await page.wait_for_selector("input[id^='c_UcnEntryBox_']",
                                                 state="visible", timeout=25000)
                except PWTimeout:
                    out["error"] = "search form (UCN box) did not render"
                    return out

                box = page.locator("input[id^='c_UcnEntryBox_']").first
                box_id = await box.get_attribute("id")
                await box.click(timeout=8000)
                await box.fill(parsed["entry"])
                try:
                    await page.evaluate("(b) => getCaseTabByUcnBoxId(b)", box_id)
                except Exception:
                    try:
                        await page.locator("input[id^='c_SubmitCaseLookupButton_']").first.click(timeout=5000)
                    except Exception:
                        pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except PWTimeout:
                    pass
                # wait for the case tab to populate (seq + Dockets present)
                try:
                    await page.wait_for_function(
                        "(s) => document.body && /Dockets/i.test(document.body.innerText) "
                        "&& document.body.innerText.includes(s)", arg=parsed["seq"], timeout=15000)
                except PWTimeout:
                    pass

                # grab the case-detail frame innerText (the frame with seq + Dockets)
                text = ""
                for fr in page.frames:
                    try:
                        t = await fr.evaluate("() => document.body ? document.body.innerText : ''")
                        if parsed["seq"] in t and re.search(r"Dockets", t):
                            text = t
                            break
                    except Exception:
                        continue

                url = page.url
                if not text:
                    out["error"] = "case-detail tab not reached (no Dockets/seq in any frame)"
                    out["url"] = url
                    return out

                # anti-fabrication: the captured case number MUST match the searched one
                sec = parse_case_text(text)
                got = re.sub(r"[^A-Za-z0-9]", "", sec.get("case_number", "")).upper()
                want = f"{parsed['year']}{parsed['division']}{parsed['seq']}"
                case_present = want in got
                (diag / f"{parsed['dashed'].replace('-', '')}.txt").write_text(text, encoding="utf-8")

                out.update({"ok": case_present, "url": url, "text": text,
                            "case_present": case_present})
                if not case_present:
                    out["error"] = (f"detail case number {sec.get('case_number')!r} does not "
                                    f"match searched {parsed['dashed']} — possible wrong result")
                elif not sec.get("capture_ok"):
                    # capture quality warning (does not fabricate; just flags)
                    out["error"] = (f"capture warning: line_max={sec.get('line_max')} "
                                    f"rows={len(sec.get('dockets', []))}")
                return out
            except PWTimeout as e:
                out["error"] = f"timeout: {str(e)[:140]}"
                return out
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {str(e)[:140]}"
                return out
            finally:
                await browser.close()

    async def scrape_case(self, case_number: str) -> DocketResult:
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
            foreclosure_type=FORECLOSURE_MORTGAGE,
        )
        result.case_url = LANDING_URL          # stable verify link (no per-case URL)
        fetched = await self.fetch_docket(case_number)
        if not fetched["ok"]:
            result.classification = "unknown"
            result.evidence_level = "auction_only"
            result.classification_reason = f"docket retrieval failed: {fetched['error']}"
            return result
        self.parse_docket(fetched["text"], result)
        self._apply_evidence_level(result)
        return result
