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
import html as _htmllib
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent


# ── Owner (defendant-homeowner) extraction ──────────────────────────────────
# The surplus belongs to the DEFENDANT HOMEOWNER (the foreclosed party). In a
# foreclosure the lender/HOA SUES the homeowner, so the bank/HOA is the PLAINTIFF
# and the homeowner is the DEFENDANT. We extract the Defendant party ONLY, and
# exclude corporate / lender / purchaser / generic parties — pulling the
# plaintiff's name (a bank) into the owner column is worse than blank.
#
# Corporate / non-homeowner markers — a Defendant whose name hits these is NOT an
# individual homeowner (junior-lienholder bank, HOA also named, an LLC purchaser,
# a govt agency). Bias to BLANK over wrong: better no owner than the wrong party.
_CORP_MARKER = re.compile(
    r"\b(bank|mortgage|trust|funding|servicing|n\.?\s?a\.?|association|assn|"
    r"condominium|condo|homeowners?|owners assoc|l\.?l\.?c|llc|inc\b|incorporated|"
    r"corp|company|\bco\.|ltd|l\.?p\.?|partnership|holdings?|capital|\bfund\b|funds\b|"
    r"department|united states|secretary|tax collector|clerk of|realty|properties|"
    r"property owners|\bgroup\b|investments?|enterprises?|management|servicing|"
    r"financial|lending|loans?|credit union|n\.a)\b", re.I)
# Generic / non-real-party defendants (unknown heirs, tenants, John Doe, etc.).
_GENERIC_PARTY = re.compile(
    r"unknown|tenant|any and all|all other|in possession|et al|john doe|jane doe|"
    r"parties claiming|lienors|creditors|whether dissolved|n/k/a|a/k/a unknown", re.I)


def _clean_name(s: str) -> str:
    return re.sub(r"\s+", " ", _htmllib.unescape(s or "")).strip().strip(",").strip()


def _is_individual_homeowner(name: str) -> bool:
    n = _clean_name(name)
    if len(n) < 3:
        return False
    if _GENERIC_PARTY.search(n) or _CORP_MARKER.search(n):
        return False
    return True


def extract_parties(html: str) -> list:
    """Parse the React Parties table → [(party_description, party_name)] in DOM
    order. Each card renders data-id="Party Description">TYPE and
    data-id="Party Name">NAME (proven on real captured HTML)."""
    descs = re.findall(r'data-id="Party Description"[^>]*>([^<]*)<', html)
    names = re.findall(r'data-id="Party Name"[^>]*>([^<]*)<', html)
    out = []
    for d, n in zip(descs, names):
        d, n = _clean_name(d), _clean_name(n)
        if d and n:
            out.append((d, n))
    return out


def owner_from_parties(parties: list) -> str:
    """The defendant homeowner: party type EXACTLY 'Defendant' (not 'Third Party
    Bidder' = purchaser, not 'Third Party Defendant' = claimant, not Plaintiff),
    an individual (corporate/generic excluded). Joint owners ('PEREZ, PEDRO &
    IVONNE') are one party-name field — kept intact. First qualifying defendant."""
    for desc, name in parties:
        if desc.strip().lower() != "defendant":
            continue
        if _is_individual_homeowner(name):
            return name
    return ""


def owner_from_caption(case_title: str) -> str:
    """FALLBACK ONLY (Parties section empty). The defendant follows 'vs' in the
    caption; strip trailing 'et al'/'Local Case Number' junk and apply the same
    corporate/generic exclusion (blank if the defendant side is a company)."""
    m = re.search(r"\bvs\.?\s+(.+?)(?:\s+et al\b|\s+Local Case|$)", case_title or "", re.I)
    if not m:
        return ""
    name = _clean_name(m.group(1))
    return name if _is_individual_homeowner(name) else ""


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
# Patterns are regex, applied case-insensitively to docket-entry TITLES (the
# aria-label filing list). Keying on titles, not full page text, avoids
# false positives from page chrome / party names. Variant-tolerant by design
# (apostrophes optional, "owner/homeowner/defendant", word-distance windows).
#
# Robustness rationale lives in data/samples/miami_dade/ROBUSTNESS.md.

# CLAIM-FILING: someone is already pursuing the surplus → lead not pursuable.
# Every pattern REQUIRES the word "surplus" near "claim"/"disburs", so generic
# rows like "Civil Cover Sheet - Claim Amount" never false-match.
CLAIM_FILED_PATTERNS = [
    r"motion for surplus funds?",
    r"motion for disbursement of surplus",
    r"order disbursing surplus",                 # disbursed = definitively dead
    r"order (?:of )?disbursement of surplus",
    r"claim\b.{0,40}surplus",                    # owner's/homeowner's/defendant's claim ... surplus
    r"surplus\b.{0,40}claim",                    # "surplus funds claim", reversed order
]

# SALE ISSUE (HARD KILL — Eric Rule 2): a motion/order to vacate the SALE (or
# the final judgment that the sale rests on), a granted cancellation, or a sale
# set aside. The vacate patterns REQUIRE a sale / final-judgment object so a
# bare "Motion to Vacate Default" (an early-case procedural motion, not about
# the sale) does NOT kill. Per-title match with a denial guard: a title that
# also contains a denial/withdrawal/moot word is NOT counted (an Order DENYING a
# motion to vacate means the sale stands → killing it would drop a good lead).
SALE_ISSUE_PATTERNS = [
    r"vacat\w*\b[^.]{0,40}\bsale",               # "Motion/Order to Vacate (the) Sale", "...Vacate Final Judgment and Sale"
    r"motion to vacate final judgment",          # vacating the FJ voids the basis for the sale
    r"set aside\b[^.]{0,40}\bsale",              # "Set Aside Sale" / "Set Aside the Foreclosure Sale"
    r"sale (?:set aside|vacated|cancell?ed)",     # "Sale Set Aside", "Sale Cancelled"
    r"cancel\w*\b[^.]{0,15}\bsale",              # "Motion to Cancel Sale", "Order Granting Motion to Cancel Sale Date"
]
SALE_ISSUE_DENIAL_GUARD = r"(?:deny|denying|denied|withdraw|withdrawn|moot|strike|stricken)"

# BANKRUPTCY — FLAG, NEVER KILL (Eric Rule 1). Any bankruptcy filing → the lead
# STAYS VISIBLE, flagged pursuable_with_caution for human review ("bankruptcy
# that stops the sale is what we want to be aware of — alert but don't negate
# it"). The active-vs-resolved split is preserved ONLY to word the reason text
# (it no longer changes whether the lead is killed — it never is on bankruptcy
# alone). A sale issue or claim filing still hard-kills regardless of bankruptcy.
BANKRUPTCY_ACTIVE_PATTERNS = [
    r"suggestion of bankruptcy",
    r"notice of (?:filing (?:of )?)?bankruptcy",
    r"bankruptcy stay",                          # "Order Case Pending Bankruptcy Stay"
    r"pending bankruptcy",
    r"automatic stay",
    r"chapter (?:7|11|13)",
]
BANKRUPTCY_RESOLVED_PATTERNS = [
    r"relief from (?:the )?(?:automatic )?stay",
    r"(?:lifting|terminating|modifying|annulling) (?:the )?(?:automatic )?stay",
    r"stay (?:lifted|terminated|modified|annulled|dissolved)",
    # bidirectional: "bankruptcy dismissed/closed/..." AND "dismissing/closing ... bankruptcy"
    r"bankruptcy\b.{0,20}(?:dismiss|discharg|clos|terminat)",
    r"(?:dismiss|discharg|clos|terminat)\w*\b.{0,20}bankruptcy",
    r"granting relief from",
    r"re-?open(?:ed|ing)?",
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

    def classify(self, result: DocketResult, final_sale_price: float) -> tuple:
        """Preserve the docket-driven classification.

        scrape_case() already runs the full Miami-Dade evidence model
        (_apply_evidence_level) — claim / active-bankruptcy / sale-issue /
        caution / clean. The enrichment CLI (core/dockets/enrich.run_one)
        calls scraper.classify(result, sale) after scrape_case to let OH
        scrapers re-run the prayer-vs-sale math; for Miami-Dade that base
        logic is wrong (it would kill a pursuable_with_caution lead on the
        'bankruptcy_resolved' token). So this override is a no-op that returns
        the classification already computed from the docket.
        """
        return (result.classification, result.classification_reason)

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
                ts = datetime.now().strftime("%H%M%S")
                try:
                    await page.screenshot(
                        path=str(diag_dir / f"{ts}-{label}.png"), full_page=True
                    )
                except Exception:
                    pass
                # Also persist the live DOM at each checkpoint. The result HTML
                # (and the HTML at each ERROR-* checkpoint) is what distinguishes
                # a v3 score reject (stuck on form / challenge overlay) from a
                # genuine no-match (searchResults rendered "no records found")
                # from a selector miss — full-page screenshots alone can be
                # ambiguous on a JS-rendered SPA. Diagnostic only; never affects
                # navigation or the v3 score (which depends on IP/UA/browser).
                try:
                    html = await page.content()
                    (diag_dir / f"{ts}-{label}.html").write_text(
                        html, encoding="utf-8"
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
    def _match_titles(titles: list[str], patterns: list[str],
                      exclude: Optional[str] = None) -> str:
        """Return the first docket-entry TITLE matching any pattern.

        Per-title matching (not a flattened blob) so an optional `exclude`
        guard can drop a title that also contains a denial/withdrawal word —
        e.g. "Order Denying Motion to Vacate Sale" matches the vacate pattern
        but is excluded, so it never kills a still-valid sale.
        """
        exc = re.compile(exclude, re.I) if exclude else None
        for t in titles:
            if exc and exc.search(t):
                continue
            for p in patterns:
                if re.search(p, t, re.I):
                    return t
        return ""

    @staticmethod
    def _any_match(haystack: str, patterns: list[str]) -> str:
        for p in patterns:
            m = re.search(p, haystack, re.I)
            if m:
                return m.group(0)
        return ""

    def parse_docket(self, html: str, result: DocketResult) -> None:
        """Populate result fields + Eric's detections from docket HTML.

        Detection keys on the docket-entry TITLES (the aria-label filing list,
        proven complete — no lazy-load truncation). The one exception is the
        bankruptcy RESOLUTION check, which also scans full page text so any sign
        the stay was lifted / case closed is caught (bias against false-kill).
        Every decision cites the matched title so it is auditable; nothing is
        fabricated.
        """
        titles = self._docket_titles(html)
        full_text = self._strip_text(html)
        stripped = full_text  # alias for caption/header regexes

        # Case caption (e.g. "WILMINGTON TRUST COMPANY, vs MANUEL ANGEL DURAN")
        cap = re.search(r"([A-Z][A-Za-z .,&'-]{4,90}\bvs\.?\s+[A-Z][A-Za-z .,&'-]{3,90})", stripped)
        if cap:
            result.case_title = cap.group(1).strip()[:200]

        # Owner = the DEFENDANT HOMEOWNER. PRIMARY source: the structured Parties
        # table (explicit Defendant typing); caption-after-"vs" is FALLBACK only
        # (messy / order not guaranteed). Never falls back to the plaintiff; if no
        # individual defendant is found, owner_name stays blank (not fabricated).
        parties = extract_parties(html)
        result.defendants = [n for d, n in parties if d.strip().lower() == "defendant"][:20]
        owner = owner_from_parties(parties)
        if not owner:
            owner = owner_from_caption(result.case_title)
        result.owner_name = owner

        fd = re.search(r"Filing Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", stripped)
        if fd:
            try:
                result.filing_date = datetime.strptime(fd.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        st = re.search(r"Case Status\s*:?\s*([A-Za-z ]{3,30})", stripped)
        if st:
            result.last_status = st.group(1).strip()[:60]
        ct = re.search(r"Case Type\s*:?\s*([A-Za-z0-9 .,&'\-]{3,60})", stripped)
        if ct:
            result.case_designation = ct.group(1).strip()[:80]

        # Store docket events (titles) for audit
        result.events = [DocketEvent(description=t[:200]).__dict__ for t in titles[:80]]

        # ── Eric's detections (title-keyed) ──
        claim_title = self._match_titles(titles, CLAIM_FILED_PATTERNS)
        sale_title = self._match_titles(titles, SALE_ISSUE_PATTERNS,
                                        exclude=SALE_ISSUE_DENIAL_GUARD)
        bk_active_title = self._match_titles(titles, BANKRUPTCY_ACTIVE_PATTERNS)
        # Resolution: scan titles AND full text (generous → avoid false-kill).
        bk_resolved = bool(
            self._match_titles(titles, BANKRUPTCY_RESOLVED_PATTERNS)
            or self._any_match(full_text, BANKRUPTCY_RESOLVED_PATTERNS)
        )

        # Base detectors (informational parity fields only — NOT used to kill;
        # bankruptcy/vacate handled by the nuanced logic above).
        result.proof_of_surplus = self.detect_proof_of_surplus(" \n ".join(titles))
        result.competing_filers = self.detect_competing_filers(" \n ".join(titles))

        # Build the docket-signal token list (audit + classifier input).
        # NOTE: bankruptcy tokens are FLAGS (caution), not kills (Eric Rule 1);
        # only claim_filed and sale_issue are kills.
        ks: list[str] = []
        if claim_title:
            result.claim_filed = True
            result.claim_type = claim_title
            if re.search(r"(?:home)?owner'?s?\s+claim", claim_title, re.I):
                result.owner_filed_claim = True
            ks.append("claim_filed")
        if sale_title:
            ks.append("sale_issue")
        # Bankruptcy: flag only. Token distinguishes active vs resolved so the
        # reason text tells the human which they're looking at — it does NOT
        # change whether the lead is killed (it never is on bankruptcy alone).
        bk_resolved_title = self._match_titles(titles, BANKRUPTCY_RESOLVED_PATTERNS)
        bk_title = bk_active_title or bk_resolved_title
        if bk_active_title or bk_resolved:
            ks.append("bankruptcy_resolved" if bk_resolved else "bankruptcy_active")
        result.kill_signals = ks

        # Stash evidence titles for the classifier's reason strings.
        result._evidence = {            # type: ignore[attr-defined]
            "claim": claim_title, "sale": sale_title,
            "bk_active": bk_active_title, "bk_resolved": bk_resolved,
            "bk_title": bk_title,
        }

    def _apply_evidence_level(self, result: DocketResult) -> None:
        """Set foreclosure_type / evidence_level / lead_status / classification.

        Precedence (Eric's clarified rules — most-disqualifying first):
          claim filed              → claim_filed      / not_pursuable / killed
          sale issue (vacate/cancel→ sale_issue_found / not_pursuable / killed  (Rule 2 hard kill)
            /set aside, granted or filed; denial-guarded)
          bankruptcy (active OR    → bankruptcy_found / pursuable_with_caution / yellow  (Rule 1 — FLAG, never kill)
            resolved)                reason preserves active-vs-resolved distinction
          competing filer circling → pursuable_with_caution / yellow
          clean docket             → no_claim_found   / pursuable / (green if proof)

        EDGE CASE (Eric): a lead with BOTH a bankruptcy AND a sale issue is a
        HARD KILL on the sale issue — sale_issue is checked before bankruptcy,
        and bankruptcy never kills. So such a lead is killed for the SALE
        reason, not the bankruptcy.
        """
        result.foreclosure_type = FORECLOSURE_MORTGAGE
        ks = set(result.kill_signals)
        ev = getattr(result, "_evidence", {})

        if "claim_filed" in ks:
            result.evidence_level = "claim_filed"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = f"claim already filed: '{ev.get('claim', result.claim_type)}'"
            return

        # Rule 2 — sale issue is a hard kill, and ranks ABOVE bankruptcy so a
        # bankruptcy+vacate lead dies for the sale reason.
        if "sale_issue" in ks:
            result.evidence_level = "sale_issue_found"
            result.lead_status = "not_pursuable"
            result.classification = "killed"
            result.classification_reason = f"sale vacated/cancelled (hard kill): '{ev.get('sale')}'"
            return

        # Rule 1 — bankruptcy FLAGS but never kills. Visible, caution.
        if "bankruptcy_active" in ks or "bankruptcy_resolved" in ks:
            result.evidence_level = "bankruptcy_found"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            bk_cite = f" (docket: '{ev.get('bk_title')}')" if ev.get("bk_title") else ""
            if "bankruptcy_resolved" in ks:
                result.classification_reason = (
                    "Bankruptcy filed, later resolved (stay lifted/dismissed) — human review" + bk_cite
                )
            else:
                result.classification_reason = (
                    "Bankruptcy filed, no resolution in docket — human review" + bk_cite
                )
            return

        if result.competing_filers:
            result.evidence_level = "pursuable_with_caution"
            result.lead_status = "pursuable_with_caution"
            result.classification = "yellow"
            result.classification_reason = (
                f"competing filer circling (no firm surplus claim yet): {result.competing_filers[0]}"
            )
            return

        # Clean docket
        result.evidence_level = "no_claim_found"
        result.lead_status = "pursuable"
        result.classification = "green" if result.proof_of_surplus else "yellow"
        result.classification_reason = (
            "docket checked — no claim, no active bankruptcy, no sale issue"
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

        # docket_url provenance: the retrieved searchResults URL carries an
        # ephemeral `?qs=<token>` that is session-bound and does NOT resolve
        # later — a broken deep-link is worse than none. Miami-Dade has no
        # stable per-case URL (same clerk-portal limitation as the other
        # counties), so we store the stable Local Case Search BASE url instead.
        # The dashboard's clipboard-copy verify flow (copy case# → open this
        # page → paste) then works, consistent with clerk_search_url for the
        # other counties. fetched["url"] (the live qs= URL) is not persisted;
        # the diagnostic HTML/screenshot artifacts already prove retrieval.
        result.case_url = LANDING_URL
        self.parse_docket(fetched["html"], result)
        self._apply_evidence_level(result)
        return result
