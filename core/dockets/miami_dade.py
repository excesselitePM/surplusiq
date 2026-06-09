"""
SurplusIQ — Miami-Dade County Docket Scraper

PHASE 1 (navigation + docket retrieval) — rewritten 2026-06-09 for the
rebuilt React/Vite OCS portal. The old ASP.NET `LocalCaseSearch.aspx`
assumption is gone; the prior "reCAPTCHA-blocked / out of scope" note was a
misdiagnosis. See data/samples/miami_dade/FINDINGS.md for the evidence.

Confirmed behaviour (headed AND headless, plain chromium.launch()):

  1. https://www2.miamidadeclerk.gov/ocs/  → React SPA (id="root").
     Page hydrates fully in headless. No captcha on the landing view.

  2. Navigation menu items are <span role="button">…</span>, NOT <a>.
     "Local Case" (text is literally "Local Case") opens the search view.

  3. Search-form fields (real names):
       #caseYear      <select>  e.g. 2017
       #caseSeq       <input>   6-digit sequence, e.g. 021344
       #caseCode      <select>  value "CA" = "CA - Circuit Civil", "CC" = County Civil
       #caseLocation  <select>  the trailing location/seq suffix, e.g. "01"/"25"
       button[type=submit] text "SEARCH"

  4. reCAPTCHA v3 INVISIBLE (score) loads on the search-form view
     (api.js?render=6Le7np8q…, size=invisible). A plain default browser with
     a realistic desktop UA scores high enough to pass — NO stealth, NO
     --disable-web-security/--no-sandbox, no typing/mouse choreography.

  5. Submit → URL becomes /ocs/searchResults?qs=<token> and the page renders
     the full "Case Information / Print Case Info" view with the docket inline
     (docket events, parties, hearings, motions) — one fetch = whole docket.

Case-number formats actually present in the Miami-Dade auction data
(docs/data/leads.json):

  Mortgage / civil foreclosure (Local Case Search applies):
    2017-021344-CA-01   year=2017 seq=021344 code=CA location=01
    2025-095651-CC-25   year=2025 seq=095651 code=CC location=25
    2019-009163-CC-05   year=2019 seq=009163 code=CC location=05

  Tax deed (NOT in Local Case Search — lives in the RealTDM portal):
    2026A00137          year=2026 tax_deed_seq=00137
    2025A00929          year=2025 tax_deed_seq=00929
  Tax-deed RealTDM routing is a SEPARATE later task. Here we only DETECT the
  type and tag it; we do not attempt Local Case Search for tax-deed numbers.
"""

from __future__ import annotations
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent


BASE_URL = "https://www2.miamidadeclerk.gov/ocs"
LANDING_URL = f"{BASE_URL}/"

# Realistic desktop UA — required for the reCAPTCHA v3 score to pass headless.
REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

FORECLOSURE_MORTGAGE = "mortgage_foreclosure"
FORECLOSURE_TAX_DEED = "tax_deed"


# ── Eric's detection terms (Miami-Dade mortgage foreclosure) ──────────────
# CLAIM-FILING: someone is already pursuing the surplus → lead not pursuable.
CLAIM_FILED_TERMS = [
    "motion for surplus funds",
    "motion for disbursement of surplus",
    "claim for mortgage foreclosure surplus",
    "owner's claim for surplus",
    "owners claim for surplus",
    "owner's claim for mortgage foreclosure surplus",
    "owners claim for mortgage foreclosure surplus",
    "claim for surplus",
    "claim to surplus",
    "order disbursing surplus",          # already disbursed = definitively dead
    "order of disbursement of surplus",
]

# KILL: sale invalid / delayed → filter out (per existing kill-signal spec).
SALE_ISSUE_TERMS = [
    "motion to vacate sale",
    "motion to vacate the sale",
    "vacate the sale",
    "set aside sale",
    "sale set aside",
    "order vacating sale",
    "sale cancelled",
    "sale canceled",
    "cancellation of sale",
    "order canceling sale",
    "order cancelling sale",
]
BANKRUPTCY_TERMS = [
    "suggestion of bankruptcy",
    "notice of bankruptcy",
    "bankruptcy",
]


def parse_miami_dade_case_number(raw: str) -> Optional[dict]:
    """
    Parse a Miami-Dade case number into search components and detect type.

    Returns a dict with a "foreclosure_type" key:

      Mortgage / civil  →  {foreclosure_type: "mortgage_foreclosure",
                            year, number, case_code, location}
        '2017-021344-CA-01'  -> year=2017 number=021344 case_code=CA location=01
        '2017021344CA01'     -> same
        '2025-095651-CC-25'  -> year=2025 number=095651 case_code=CC location=25

      Tax deed          →  {foreclosure_type: "tax_deed",
                            year, tax_deed_number, raw}
        '2026A00137'         -> year=2026 tax_deed_number=00137
        '2025A00929'         -> year=2025 tax_deed_number=00929

    Returns None if not parseable in either shape.
    """
    if not raw:
        return None

    # Strip auction suffix like " (12345)" if present
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())

    # ── Tax-deed shape: YYYY 'A' NNNNN  (the 'A' marks a tax-deed certificate)
    # Check this BEFORE the civil shape so "2026A00137" isn't mis-stripped.
    td = re.match(r"^\s*(\d{4})\s*A\s*(\d{4,6})\s*$", cleaned, re.IGNORECASE)
    if td:
        return {
            "foreclosure_type": FORECLOSURE_TAX_DEED,
            "year": int(td.group(1)),
            "tax_deed_number": td.group(2),
            "raw": cleaned.upper().replace(" ", ""),
        }

    # ── Mortgage / civil shape: YYYY + 6-digit number + 2-letter code + 2-digit location
    compact = re.sub(r"[-\s]", "", cleaned).upper()
    m = re.match(r"^(\d{4})(\d{6})([A-Z]{2})(\d{2})$", compact)
    if not m:
        return None

    return {
        "foreclosure_type": FORECLOSURE_MORTGAGE,
        "year":      int(m.group(1)),
        "number":    m.group(2),    # 6-digit string, preserve leading zeros
        "case_code": m.group(3),    # "CA" Circuit Civil, "CC" County Civil
        "location":  m.group(4),    # "01" / "25" / "05" — the trailing location
    }


class MiamiDadeDocketScraper(DocketScraper):

    county_id = "miami-dade-fl"
    county_name = "Miami-Dade"

    # ── Phase 1 core: navigate the SPA and return the raw docket HTML ──────

    async def fetch_docket_html(self, case_number: str, attempts: int = 2) -> dict:
        """
        Drive the React SPA for one mortgage-foreclosure case and return:

          {
            "ok": bool,
            "url": str,            # final searchResults URL on success
            "html": str,           # full Case Information page HTML on success
            "foreclosure_type": str,
            "parsed": dict|None,
            "error": str,          # populated when ok is False
          }

        Tax-deed case numbers are detected and short-circuited (ok=False,
        error="tax_deed") — RealTDM routing is a separate later task.

        Reusable building block; the parser/classifier (Phase 2) consumes the
        returned html. Does not fabricate anything: on any failure html="".

        Retries up to `attempts` times on *retrieval* errors only (the SPA /
        v3 occasionally doesn't land on first try). Parse failures and tax-deed
        short-circuits never retry.
        """
        parsed = parse_miami_dade_case_number(case_number)
        if not parsed:
            return {"ok": False, "url": "", "html": "",
                    "foreclosure_type": "", "parsed": None,
                    "error": f"case number not parseable: {case_number}"}
        if parsed["foreclosure_type"] == FORECLOSURE_TAX_DEED:
            return {"ok": False, "url": "", "html": "",
                    "foreclosure_type": FORECLOSURE_TAX_DEED, "parsed": parsed,
                    "error": "tax_deed (RealTDM routing not implemented — separate task)"}

        last = None
        for i in range(max(1, attempts)):
            last = await self._fetch_once(case_number, parsed)
            if last["ok"]:
                return last
        return last

    async def _fetch_once(self, case_number: str, parsed: dict) -> dict:
        out = {
            "ok": False, "url": "", "html": "",
            "foreclosure_type": parsed["foreclosure_type"], "parsed": parsed, "error": "",
        }

        diag_dir = Path("data/diagnostics/miami-dade-fl")
        diag_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            # Plain default launch — investigation proved this passes v3.
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1400, "height": 900},
                ignore_https_errors=True,
                user_agent=REAL_UA,
            )
            page = await context.new_page()

            async def snap(label):
                try:
                    ts = datetime.now().strftime("%H%M%S")
                    await page.screenshot(
                        path=str(diag_dir / f"{ts}-{label}.png"), full_page=True
                    )
                except Exception:
                    pass

            try:
                # ── Step 1: landing (SPA) ──
                await page.goto(LANDING_URL, wait_until="load", timeout=45000)
                # Wait for the SPA nav to hydrate (the "Local Case" button).
                await page.wait_for_selector(
                    "span[role='button']:has-text('Local Case')", timeout=20000
                )
                await snap("01-landing")

                # ── Step 2: open Local Case Search view ──
                clicked = False
                for sel in (
                    "span[role='button']:has-text('Local Case')",
                    "span.subitem-color:has-text('Local Case')",
                    "[role='button']:has-text('Local Case')",
                ):
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(timeout=8000)
                        clicked = True
                        break
                if not clicked:
                    out["error"] = "could not find 'Local Case' nav button"
                    await snap("ERROR-no-nav")
                    return out

                # The search form renders the year select once the view mounts.
                await page.wait_for_selector("#caseYear", timeout=20000)
                await snap("02-search-form")

                # ── Step 3: fill the form ──
                filled, fill_err = await self._fill_search_form(page, parsed)
                if not filled:
                    out["error"] = f"form fill failed: {fill_err}"
                    await snap("ERROR-form-fill")
                    return out
                await snap("03-form-filled")

                # ── Step 4: submit and wait for the results route ──
                submitted = False
                for sel in (
                    "button[type='submit']:has-text('SEARCH')",
                    "button:has-text('SEARCH')",
                    "button[type='submit']",
                ):
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click(timeout=5000)
                        submitted = True
                        break
                if not submitted:
                    out["error"] = "could not click SEARCH button"
                    await snap("ERROR-no-submit")
                    return out

                try:
                    await page.wait_for_url("**/searchResults*", timeout=25000)
                except PWTimeout:
                    out["error"] = (
                        f"no navigation to searchResults (url={page.url}) — "
                        f"possible no-match or v3 score reject"
                    )
                    await snap("ERROR-no-results-route")
                    return out

                # Let the Case Information view render its content in place.
                try:
                    await page.wait_for_selector(
                        "text=/Case (Information|Details)/i", timeout=15000
                    )
                except PWTimeout:
                    pass
                await page.wait_for_load_state("networkidle", timeout=15000)
                await snap("04-results")

                html = await page.content()

                # Sanity: the searched case number must appear in the result —
                # anti-fabrication / wrong-case guard (CLAUDE.md core rule).
                if parsed["number"] not in html:
                    out["error"] = (
                        f"result page does not contain searched seq "
                        f"{parsed['number']} — possible empty/wrong result"
                    )
                    out["url"] = page.url
                    return out

                out["ok"] = True
                out["url"] = page.url
                out["html"] = html
                return out

            except PWTimeout as e:
                out["error"] = f"timeout: {str(e)[:160]}"
                await snap("ERROR-timeout")
                return out
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
                await snap("ERROR-exception")
                return out
            finally:
                await browser.close()

    async def _fill_search_form(self, page: Page, parsed: dict) -> tuple[bool, str]:
        """Fill #caseYear / #caseSeq / #caseCode / #caseLocation."""
        # Year (select)
        try:
            await page.locator("#caseYear").first.select_option(
                value=str(parsed["year"]), timeout=5000
            )
        except Exception as e:
            return False, f"year select: {str(e)[:80]}"

        # Sequence number (text input)
        try:
            seq = page.locator("#caseSeq").first
            await seq.click(timeout=3000)
            await seq.fill("", timeout=3000)
            await seq.type(parsed["number"], delay=40)
        except Exception as e:
            return False, f"seq input: {str(e)[:80]}"

        # Case code (select) — value is the 2-letter code, e.g. "CA"/"CC"
        try:
            await page.locator("#caseCode").first.select_option(
                value=parsed["case_code"], timeout=5000
            )
        except Exception as e:
            return False, f"code select: {str(e)[:80]}"

        # Location (select) — populates after the code is chosen on some views.
        try:
            await page.wait_for_timeout(600)
            loc = page.locator("#caseLocation").first
            if await loc.count() > 0:
                opts = await loc.evaluate(
                    "el => Array.from(el.options).map(o => o.value).filter(v => v)"
                )
                want = parsed["location"]
                if want in opts:
                    await loc.select_option(value=want, timeout=5000)
                elif len(opts) == 1:
                    # single valid location — use it
                    await loc.select_option(value=opts[0], timeout=5000)
                elif opts:
                    # best effort: try the parsed value anyway
                    try:
                        await loc.select_option(value=want, timeout=3000)
                    except Exception:
                        await loc.select_option(value=opts[0], timeout=3000)
        except Exception as e:
            # Location is not always strictly required; log but don't hard-fail.
            return True, f"location best-effort warning: {str(e)[:80]}"

        return True, ""

    # ── Phase 2: parse docket HTML → Eric's review fields ─────────────────

    @staticmethod
    def _docket_titles(html: str) -> list[str]:
        """Extract docket-entry titles from the Case Information page.

        Each docket/filing card carries aria-label="View details for <TITLE>"
        and the same TITLE in a <p class="fs-5 fw-bold">. Using the aria-label
        is precise — it targets actual filing cards, not page chrome.
        """
        titles = re.findall(r'aria-label="View details for ([^"]+)"', html)
        # De-dupe but preserve order
        seen, out = set(), []
        for t in titles:
            tn = re.sub(r"\s+", " ", t).strip()
            key = tn.lower()
            if tn and key not in seen:
                seen.add(key)
                out.append(tn)
        return out

    @staticmethod
    def _strip_text(html: str) -> str:
        import html as _h
        txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        txt = re.sub(r"<[^>]+>", " ", txt)
        return re.sub(r"\s+", " ", _h.unescape(txt))

    @staticmethod
    def _first_match(haystack: str, terms: list[str]) -> str:
        """Return the first term in `terms` found in lowercased haystack."""
        for t in terms:
            if t in haystack:
                return t
        return ""

    def parse_docket(self, html: str, result: DocketResult) -> None:
        """Populate result fields + Eric's taxonomy from docket HTML.

        Detection runs over BOTH the precise docket-card titles and the full
        stripped page text (recall), but classification reasons cite the
        matched phrase so every decision is auditable. No fabrication: amounts
        are not invented; only docket-present phrases drive the classification.
        """
        titles = self._docket_titles(html)
        title_blob = " \n ".join(titles).lower()
        full_text = self._strip_text(html).lower()
        # Card titles are the authoritative filing list; full_text is a backstop.
        scan = title_blob + " \n " + full_text

        # Case caption (e.g. "WILMINGTON TRUST COMPANY, vs MANUEL ANGEL DURAN")
        cap = re.search(r"([A-Z][A-Za-z .,&'-]{4,90}\bvs\.?\s+[A-Z][A-Za-z .,&'-]{3,90})",
                        self._strip_text(html))
        if cap:
            result.case_title = cap.group(1).strip()[:200]

        # Filing date
        fd = re.search(r"Filing Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", self._strip_text(html))
        if fd:
            try:
                result.filing_date = datetime.strptime(fd.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Case status / type
        st = re.search(r"Case Status\s*:?\s*([A-Za-z ]{3,30})", self._strip_text(html))
        if st:
            result.last_status = st.group(1).strip()[:60]
        ct = re.search(r"Case Type\s*:?\s*([A-Za-z0-9 .,&'\-]{3,60})", self._strip_text(html))
        if ct:
            result.case_designation = ct.group(1).strip()[:80]

        # Store docket events (titles) for audit
        result.events = [DocketEvent(description=t[:200]).__dict__ for t in titles[:80]]
        result.last_activity_date = result.last_activity_date or ""

        # ── Eric's detections ──
        claim = self._first_match(scan, CLAIM_FILED_TERMS)
        sale_issue = self._first_match(scan, SALE_ISSUE_TERMS)
        bankruptcy = self._first_match(scan, BANKRUPTCY_TERMS)

        # Standard base detectors (kept for downstream fields / parity)
        result.kill_signals = self.detect_kill_signals(scan)
        result.proof_of_surplus = self.detect_proof_of_surplus(scan)
        result.competing_filers = self.detect_competing_filers(scan)

        if claim:
            result.claim_filed = True
            result.claim_type = claim
            if re.search(r"owner'?s? claim", scan):
                result.owner_filed_claim = True

    def _apply_evidence_level(self, result: DocketResult) -> None:
        """Set foreclosure_type / evidence_level / lead_status / classification.

        Precedence (most-disqualifying first):
          claim filed     → claim_filed     / not_pursuable / killed
          bankruptcy      → bankruptcy_found / not_pursuable / killed
          sale issue      → sale_issue_found / not_pursuable / killed
          competing filer → pursuable_with_caution (someone circling, no firm claim)
          clean docket    → no_claim_found  / pursuable     / (base.classify)
        """
        result.foreclosure_type = FORECLOSURE_MORTGAGE

        if result.claim_filed:
            result.evidence_level = "claim_filed"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = f"claim already filed: '{result.claim_type}'"
            return

        if "bankruptcy" in result.kill_signals:
            result.evidence_level = "bankruptcy_found"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = "bankruptcy signal present in docket"
            return

        sale_issue_signals = {"motion_to_vacate", "sale_vacated"} & set(result.kill_signals)
        if sale_issue_signals:
            result.evidence_level = "sale_issue_found"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = f"sale issue: {sorted(sale_issue_signals)[0]}"
            return

        # Any other kill signal still kills (already_disbursed, escheated, ...)
        if result.kill_signals:
            result.evidence_level = "not_pursuable"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = f"kill signal: {result.kill_signals[0]}"
            return

        if result.competing_filers:
            result.evidence_level = "pursuable_with_caution"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                f"competing filer circling (no firm claim yet): {result.competing_filers[0]}"
            )
            return

        # Clean docket
        result.evidence_level = "no_claim_found"
        result.lead_status = "pursuable"
        result.classification = "green" if result.proof_of_surplus else "yellow"
        result.classification_reason = (
            "docket checked — no claim, no kill signals"
            + (", proof of surplus filed" if result.proof_of_surplus else "")
        )

    # ── scrape_case: full Phase-2 pipeline ────────────────────────────────

    async def scrape_case(self, case_number: str) -> DocketResult:
        """
        Retrieve the docket (Phase 1) then parse + classify it (Phase 2):
        docket HTML → claim/kill detection → evidence_level + lead_status +
        foreclosure_type + classification. Never fabricates — on retrieval
        failure the result stays classification="unknown"/evidence auction_only.
        """
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
        )

        fetched = await self.fetch_docket_html(case_number)
        result.foreclosure_type = fetched.get("foreclosure_type") or ""

        if not fetched["ok"]:
            result.classification = "unknown"
            result.evidence_level = "auction_only"
            result.classification_reason = f"docket retrieval failed: {fetched['error']}"
            return result

        result.case_url = fetched["url"]
        self.parse_docket(fetched["html"], result)
        self._apply_evidence_level(result)
        return result
