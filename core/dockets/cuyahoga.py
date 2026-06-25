"""
SurplusIQ — Cuyahoga County Docket Scraper

Cuyahoga is the easiest Ohio county because the clerk page shows the
'Prayer Amount' directly as a structured field — no PDF parsing required
to get the true debt amount.

Navigation flow (from reconnaissance):

  1. https://cpdocket.cp.cuyahogacounty.gov/
     → Click "Yes" on the Conditions of Use page

  2. /Search.aspx
     → Click "CIVIL SEARCH BY CASE" radio button (form fields appear)

  3. Form fields:
     - Case Type dropdown: select "CIVIL"
     - Case Year dropdown: select YYYY (e.g. "2025")
     - Case Number text input: enter sequence (e.g. "110711")
     → Click "Submit Search"

  4. /CV_CaseInformation_Summary.aspx?q=<token>
     → Page shows: Case Title, Filing Date, Last Status, Last Disposition,
       Prayer Amount, plus nav links to Docket | Parties | etc.

  5. Visit the Docket sub-page for kill-signal scanning + event list

Case number format Eric showed:
  Raw:     CV25110711 (61657)         (from auction scraper)
  Parsed:  type=CV, year=2025, number=110711
  Stripped suffix: "(61657)" is the auction batch ID, not part of case number

Note: foreclosure cases sometimes have type "FORECLOSURE MARSH. OF LIEN" but
they still file under CIVIL case type for the search.
"""

from __future__ import annotations
import re
import io
import asyncio
from pathlib import Path
from datetime import datetime, date
from typing import Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent
from .oh_debt import parse_cuyahoga_mortgage_debt, components_dict


BASE_URL = "https://cpdocket.cp.cuyahogacounty.gov"
DISCLAIMER_URL = f"{BASE_URL}/"
SEARCH_URL = f"{BASE_URL}/Search.aspx"


def parse_cuyahoga_case_number(raw: str) -> Optional[dict]:
    """
    Parse a Cuyahoga case number into search components.

    Accepts variations:
      'CV25110711'             -> {type: CIVIL, year: 2025, number: 110711}
      'CV25110711 (61657)'     -> same (strips the auction suffix)
      'CV-25-110711'           -> same
      'CV-2025-110711'         -> same (already long-form year)

    Returns None if not parseable.
    """
    if not raw:
        return None

    # Strip auction suffix like " (61657)"
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())
    # Strip dashes and spaces
    cleaned = re.sub(r"[-\s]", "", cleaned).upper()

    # Cuyahoga uses 2-digit year format: CV + YY + 6-digit-case-number
    # Try strict 2-digit-year pattern first
    m = re.match(r"^(CV)(\d{2})(\d{6})$", cleaned)
    if m:
        case_type = m.group(1)
        year_raw = m.group(2)
        number = m.group(3)
    else:
        # Fallback: 4-digit year format CV + YYYY + 6-digit case
        m = re.match(r"^(CV)(\d{4})(\d{6,})$", cleaned)
        if not m:
            return None
        case_type = m.group(1)
        year_raw = m.group(2)
        number = m.group(3)

    # Normalize 2-digit year to 4-digit
    if len(year_raw) == 2:
        yr = int(year_raw)
        year = 2000 + yr if yr <= 30 else 1900 + yr
    else:
        year = int(year_raw)

    return {
        "case_type":   "CIVIL",   # search dropdown value
        "year":        year,
        "number":      number,
        "case_prefix": case_type,
    }


class CuyahogaDocketScraper(DocketScraper):

    county_id = "cuyahoga-oh"
    county_name = "Cuyahoga"

    async def scrape_case(self, case_number: str) -> DocketResult:
        """Run the full scrape against one case. Returns a DocketResult."""
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
        )

        parsed = parse_cuyahoga_case_number(case_number)
        if not parsed:
            result.classification = "unknown"
            result.classification_reason = f"case number not parseable: {case_number}"
            return result

        # Screenshot dir for debugging
        from pathlib import Path as _P
        diag_dir = _P.home() / "Desktop/surplusiq/data/diagnostics/cuyahoga-oh"
        diag_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()

            async def snap(label):
                try:
                    await page.screenshot(path=str(diag_dir / f"{label}.png"), full_page=True)
                    (diag_dir / f"{label}.html").write_text(await page.content())
                    print(f"      📸 {label}: {page.url}")
                except Exception as e:
                    print(f"      ⚠ snap failed: {e}")

            try:
                print(f"      ▶ step 1: disclaimer")
                await self._accept_disclaimer(page)
                await snap("01_after_disclaimer")

                print(f"      ▶ step 2: search form")
                await self._navigate_to_search_form(page)
                await snap("02_search_form_open")

                print(f"      ▶ step 3: submit search")
                found = await self._submit_search(page, parsed)
                await snap("03_after_submit")
                print(f"      → landed at: {page.url}")
                print(f"      → search succeeded? {found}")

                if not found:
                    result.classification = "unknown"
                    result.classification_reason = f"search did not land on case summary. URL: {page.url}"
                    return result

                result.case_url = page.url
                print(f"      ▶ step 4: summary page")
                await self._scrape_summary_page(page, result)
                print(f"      → prayer={result.prayer_amount}, title={result.case_title[:40]}")

                print(f"      ▶ step 5: docket page")
                await self._scrape_docket_page(page, result)
                await snap("04_docket")
                print(f"      → events={len(result.events)}, kill={result.kill_signals}")

                print(f"      ▶ step 6: parties page")
                await self._scrape_parties_page(page, result)
                await snap("05_parties")
                print(f"      → defendants={len(result.defendants)}")

                result.classification, result.classification_reason = self.classify(result, 0.0)

            except PWTimeout as e:
                await snap("99_timeout")
                result.classification = "unknown"
                result.classification_reason = f"timeout: {e}"
                print(f"      ❌ TIMEOUT: {e}")
            except Exception as e:
                await snap("99_error")
                result.classification = "unknown"
                result.classification_reason = f"scrape error: {type(e).__name__}: {e}"
                print(f"      ❌ ERROR: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
            finally:
                await browser.close()

        return result

    # ─── Step 1: Accept Conditions of Use ───
    async def _accept_disclaimer(self, page: Page) -> None:
        await page.goto(DISCLAIMER_URL, wait_until="domcontentloaded", timeout=30000)
        # The "Yes" button is an <input type="button" value="Yes" />
        for sel in [
            "input[type='button'][value='Yes']",
            "input[type='submit'][value='Yes']",
            "button:has-text('Yes')",
        ]:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                return
        raise RuntimeError("Could not find 'Yes' button on disclaimer page")

    # ─── Step 2: Click "CIVIL SEARCH BY CASE" radio to reveal form ───
    async def _navigate_to_search_form(self, page: Page) -> None:
        if "Search.aspx" not in page.url:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        # The radio's label text is "CIVIL SEARCH BY CASE"
        for sel in [
            "input[type='radio'][value*='CivilCase']",
            "input[type='radio'][id*='CivilCase']",
            "text=CIVIL SEARCH BY CASE",
        ]:
            el = await page.query_selector(sel)
            if el:
                await el.click()
                # _submit_search's first action is wait_for_selector on the
                # case-type dropdown, which is the condition we want — the
                # blind 800ms sleep added nothing useful.
                return
        # Fallback — click the label text
        await page.click("text=CIVIL SEARCH BY CASE")

    # ─── Step 3: Fill form and submit ───
    async def _submit_search(self, page: Page, parsed: dict) -> bool:
        """
        Cuyahoga's actual form uses ASP.NET WebForms with these IDs:
          - Case Type select:    select#SheetContentPlaceHolder_civilCaseSearch_ddlCaseType
          - Case Year select:    select#SheetContentPlaceHolder_civilCaseSearch_ddlCaseYear
          - Case Number input:   input#SheetContentPlaceHolder_civilCaseSearch_txtCaseNum
          - Submit button:       input#SheetContentPlaceHolder_civilCaseSearch_btnSubmitCase
        The dropdowns trigger ASP.NET PostBacks so we wait for networkidle after each.
        """
        # ── Case Type ──
        type_sel = "select#SheetContentPlaceHolder_civilCaseSearch_ddlCaseType"
        await page.wait_for_selector(type_sel, timeout=10000)
        await page.select_option(type_sel, value="CIVIL")
        # networkidle already handles the postback settling; drop the
        # follow-up 1000ms blind sleep that fired even on cache hits.
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        # ── Case Year ──
        year_sel = "select#SheetContentPlaceHolder_civilCaseSearch_ddlCaseYear"
        await page.wait_for_selector(year_sel, timeout=10000)
        await page.select_option(year_sel, value=str(parsed["year"]))
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        # ── Case Number ──
        num_sel = "input#SheetContentPlaceHolder_civilCaseSearch_txtCaseNum"
        await page.wait_for_selector(num_sel, timeout=10000)
        await page.fill(num_sel, parsed["number"])

        # ── Submit ──
        # Replace the post-submit 2000ms blind sleep with a URL-based
        # condition: Cuyahoga's CaseSummary postback always lands on a URL
        # containing "CaseInformation_Summary" or "CaseSummary" (this exact
        # check was already the return value below — it's the page's own
        # success marker). Returns the instant the redirect lands.
        submit_sel = "input#SheetContentPlaceHolder_civilCaseSearch_btnSubmitCase"
        await page.click(submit_sel)
        try:
            await page.wait_for_url(
                lambda u: "CaseInformation_Summary" in u or "CaseSummary" in u,
                timeout=10000,
            )
        except PWTimeout:
            # Search failed (no such case) — page stays on the search form.
            # Caller treats False return as "not found".
            pass

        return "CaseInformation_Summary" in page.url or "CaseSummary" in page.url

        # ─── Step 4: Scrape Case Information Summary page ───
    async def _scrape_summary_page(self, page: Page, result: DocketResult) -> None:
        text = await page.inner_text("body")

        # Case title (e.g. "PIC FUND I, LLC vs. PROP4 LLC, ET AL")
        m = re.search(r"CV-\d{2}-\d+\s+(.+?)(?=\nCase Summary|\n\s*\|)", text, re.DOTALL)
        if m:
            result.case_title = m.group(1).strip().replace("\n", " ")

        # Field rows: each is "Label: Value" on its own line
        fields = {
            "case_designation":     r"Case Designation:\s*(.+)",
            "filing_date":          r"Filing Date:\s*(\d{2}/\d{2}/\d{4})",
            "last_status":          r"Last Status:\s*(\w+)",
            "last_disposition":     r"Last Disposition:\s*(\w+)",
            "last_disposition_date": r"Last Disposition Date:\s*(\d{2}/\d{2}/\d{4})",
        }
        for field_name, pattern in fields.items():
            m = re.search(pattern, text)
            if m:
                val = m.group(1).strip()
                # Convert MM/DD/YYYY to YYYY-MM-DD for dates
                if "date" in field_name and "/" in val:
                    try:
                        mm, dd, yyyy = val.split("/")
                        val = f"{yyyy}-{mm}-{dd}"
                    except ValueError:
                        pass
                setattr(result, field_name if field_name != "last_disposition_date" else "last_activity_date", val)

        # Prayer Amount — the critical field. Cuyahoga's "Prayer Amount"
        # field is unreliable for many older foreclosure cases: it often
        # holds court costs / filing fees / small-claim ancillary amounts
        # ($100-$3K range), not the actual judgment principal. Real
        # foreclosure judgments are in the $50K-$300K+ range. We reject
        # anything under MIN_PLAUSIBLE_PRAYER as not-found so the
        # downstream true_surplus math doesn't credit court-cost noise as
        # a real judgment. (Anti-fabrication: prefer "unknown" over a
        # bogus tiny number that classifies a $50K-surplus property as
        # GREEN/YELLOW.)
        MIN_PLAUSIBLE_PRAYER = 10000.0
        m = re.search(r"Prayer Amount:\s*\$?([\d,]+(?:\.\d{2})?)", text)
        if m:
            try:
                raw = float(m.group(1).replace(",", ""))
                if raw >= MIN_PLAUSIBLE_PRAYER:
                    result.prayer_amount = raw
                    result.debt_source = "prayer_field"
                else:
                    print(f"      ⚠ rejected prayer_field=${raw:,.2f} as implausible "
                          f"for a foreclosure (floor: ${MIN_PLAUSIBLE_PRAYER:,.0f}); "
                          f"treating as not-found")
                    result.classification_reason = (
                        f"prayer_field=${raw:,.2f} rejected as below ${MIN_PLAUSIBLE_PRAYER:,.0f} "
                        f"foreclosure-judgment plausibility floor"
                    )
            except ValueError:
                pass

        # Scan summary text for any kill signals or proof
        result.kill_signals = self.detect_kill_signals(text)
        proof = self.detect_proof_of_surplus(text)
        if proof:
            result.proof_of_surplus = proof

    # ─── Step 5: Scrape Docket sub-page for events + kill signals ───
    async def _scrape_docket_page(self, page: Page, result: DocketResult) -> None:
        # Click the "Docket" link in the case nav
        try:
            await page.click("a:has-text('Docket')")
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            # ASP.NET PostBack settle. networkidle catches the back-half
            # of the panel re-render that domcontentloaded misses.
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
        except Exception:
            return  # Docket page not accessible; skip

        docket_text = await page.inner_text("body")

        # Scan full docket text for signals
        result.kill_signals = list(set(result.kill_signals + self.detect_kill_signals(docket_text)))
        result.competing_filers = self.detect_competing_filers(docket_text)
        proof = self.detect_proof_of_surplus(docket_text)
        if proof and not result.proof_of_surplus:
            result.proof_of_surplus = proof

        # Check for owner's claim explicitly
        if re.search(r"owner'?s? claim", docket_text, re.IGNORECASE):
            result.owner_filed_claim = True

        # Extract docket events (table rows with date + description)
        # Cuyahoga's docket events are typically in a table with date column + description column
        rows = await page.query_selector_all("table tr")
        events = []
        for row in rows:
            row_text = (await row.inner_text()).strip()
            if not row_text:
                continue
            # Look for "MM/DD/YYYY" date at start of row
            m = re.match(r"^(\d{2}/\d{2}/\d{4})\s+(.+)", row_text, re.DOTALL)
            if m:
                mm, dd, yyyy = m.group(1).split("/")
                events.append(DocketEvent(
                    filing_date=f"{yyyy}-{mm}-{dd}",
                    description=m.group(2).strip()[:200],
                ))
        result.events = [e.__dict__ for e in events[:50]]  # cap at 50 events

        # Update last_activity_date from most recent event
        if events:
            sorted_events = sorted(events, key=lambda e: e.filing_date, reverse=True)
            result.last_activity_date = sorted_events[0].filing_date

        # Conservative-debt: fetch the judgment-decree document and parse it. On
        # success this STORES debt components (enrich computes the sale-date math)
        # and supersedes the prayer_field principal-only figure. On failure the
        # prayer_field set in _scrape_summary_page remains as the fallback.
        await self._fetch_decree_and_parse(page, result)

    async def _fetch_decree_and_parse(self, page: Page, result: DocketResult) -> None:
        """Find the judgment-decree document on the docket page (DisplayImageList.aspx
        link — proven reachable), fetch it, and run parse_cuyahoga_mortgage_debt. The
        decree carries the principal + rate + from-date + split-balance oh_debt needs;
        the prayer_field (complaint prayer, principal-only) is the fallback."""
        try:
            rows = await page.evaluate(r"""() =>
                Array.from(document.querySelectorAll('table tr')).map(tr => ({
                    t:(tr.innerText||'').replace(/\s+/g,' ').trim(),
                    a:Array.from(tr.querySelectorAll('a[href]')).map(x=>x.getAttribute('href'))
                })).filter(r => r.t && r.a.length)""")
        except Exception as e:
            print(f"      ⚠ decree row scan failed: {e}")
            return

        want = re.compile(r"decree of foreclosure|judgment entry adopting|"
                          r"adopting (the )?magistrate.?s decision|magistrate.?s decision|"
                          r"final judgment entry", re.I)
        # Skip non-decree procedural rows (hearings/notices/orders of sale) AND
        # released/vacated entries — these are not the judgment decree.
        skip = re.compile(r"satisf|vacat|release|dismiss|withdraw|denied|continu|"
                          r"hearing|called for|scheduled|notice|praecipe|writ|order of sale",
                          re.I)
        cands = [r for r in rows if want.search(r["t"]) and not skip.search(r["t"])]
        print(f"      → decree candidates: {len(cands)}")

        req = page.context.request
        diag = Path("data/diagnostics/cuyahoga-oh")
        for r in cands[:6]:
            href = r["a"][0]
            url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
            try:
                resp = await req.get(url, timeout=20000)
                body = await resp.body()
                if not body or body[:4] != b"%PDF":
                    continue
                import pdfplumber
                text = ""
                with pdfplumber.open(io.BytesIO(body)) as pdf:
                    for pg in pdf.pages[:15]:
                        text += (pg.extract_text() or "") + "\n"
                parsed = parse_cuyahoga_mortgage_debt(text)
                if parsed.is_tax_decree:
                    print(f"      ⏭ [{r['t'][:40]}] is a tax decree — skipping")
                    continue
                if parsed.principal < 10000.0:
                    # Only a REAL mortgage decree whose principal anchor we missed is
                    # worth surfacing. Procedural/tax docs (no "plus interest"/"principal
                    # balance"/"in rem judgment" language) parse to $0 too — don't cry
                    # wolf on those, or a true miss drowns in the noise.
                    looks_mortgage = re.search(
                        r"plus\s+interest|principal\s+(?:balance|amount)|promissory\s+note|"
                        r"in\s+rem\s+judgment", text, re.I)
                    if parsed.principal == 0.0 and looks_mortgage:
                        print(f"      ⚠ ANCHOR MISS (principal $0) on a MORTGAGE-like decree "
                              f"[{r['t'][:45]}] — saving UNPARSED for review")
                        try:
                            diag.mkdir(parents=True, exist_ok=True)
                            sc = re.sub(r"[^A-Za-z0-9_-]+", "_", result.case_number)
                            ts = datetime.now().strftime("%H%M%S")
                            (diag / f"{ts}-{sc}-UNPARSED.txt").write_text(text, encoding="utf-8")
                        except Exception:
                            pass
                    continue
                result.debt_components = components_dict(parsed)
                result.prayer_amount = 0.0          # computed in enrich; supersedes prayer_field
                result.debt_source = "oh_mortgage_decree"
                print(f"      ✅ Cuyahoga decree [{r['t'][:40]}]: principal "
                      f"${parsed.principal:,.2f}  base ${parsed.interest_base:,.2f}  rate="
                      f"{(str(round(parsed.interest_rate*100,3))+'%') if parsed.interest_rate else 'none'}")
                for n in parsed.notes:
                    print(f"         · {n}")
                try:
                    diag.mkdir(parents=True, exist_ok=True)
                    sc = re.sub(r"[^A-Za-z0-9_-]+", "_", result.case_number)
                    ts = datetime.now().strftime("%H%M%S")
                    (diag / f"{ts}-{sc}-decree.txt").write_text(text, encoding="utf-8")
                except Exception:
                    pass
                return
            except Exception as e:
                print(f"      ⚠ decree fetch [{r['t'][:30]}] failed: {type(e).__name__}: {e}")

        if result.debt_source != "oh_mortgage_decree":
            print(f"      → no parseable decree; keeping prayer_field "
                  f"${result.prayer_amount:,.2f} as fallback (flagged uncertain downstream)")

    # ─── Step 6: Scrape Parties sub-page for defendants ───
    async def _scrape_parties_page(self, page: Page, result: DocketResult) -> None:
        try:
            await page.click("a:has-text('Parties')")
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
        except Exception:
            return

        parties_text = await page.inner_text("body")

        # Extract plaintiff and defendants
        # Cuyahoga lists parties in sections labeled "PLAINTIFF" / "DEFENDANT"
        plaintiff_m = re.search(r"PLAINTIFF\s*:?\s*\n([^\n]+)", parties_text, re.IGNORECASE)
        if plaintiff_m:
            result.plaintiff = plaintiff_m.group(1).strip()

        # Find all defendant blocks
        defendants = []
        for m in re.finditer(r"DEFENDANT\s*:?\s*\n([^\n]+)", parties_text, re.IGNORECASE):
            name = m.group(1).strip()
            if name and name not in defendants:
                defendants.append(name)
        result.defendants = defendants

        # Identify creditors among defendants (entities beyond the obvious homeowner)
        # Heuristic: if a defendant name contains "LLC", "BANK", "TREASURER", "IRS",
        # "STATE OF", "COUNTY", "CITY OF", etc., it's a creditor not the homeowner
        creditor_keywords = [
            "LLC", "BANK", "TREASURER", "IRS", "STATE OF", "COUNTY", "CITY OF",
            "REVENUE", "DEPARTMENT", "ASSOCIATION", "TRUST", "FINANCIAL"
        ]
        for name in defendants:
            name_upper = name.upper()
            if any(kw in name_upper for kw in creditor_keywords):
                result.additional_parties.append(name)
