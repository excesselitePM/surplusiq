"""
SurplusIQ — Montgomery County Docket Scraper

Montgomery County uses the "Public Records Online v3" (PRO) portal at:
  https://pro.mcohio.org

No Cloudflare detected. Standard ASP.NET Razor app (.cshtml).
Has a disclaimer modal (javascript:openDisclaimer()) and nav links
for Search / Results / Case Info.

Key difference from Cuyahoga: no structured prayer amount field.
Must extract debt from summary judgment PDF via pdfplumber.

Case number format from auction scraper:
  Raw:     2025 CV 02948-1 (0)
  Parsed:  year=2025, type=CV, number=02948, suffix=-1 (parcel), (0)=auction ID
  Search:  '2025 CV 02948' (strip parcel suffix and auction ID)

Navigation flow (expected):
  1. https://pro.mcohio.org — landing page with disclaimer modal
  2. Accept disclaimer → nav to Search
  3. Search by case number (paste full, e.g. '2025 CV 02948')
  4. Case detail page → summary, docket entries, parties
  5. Find judgment entry → open PDF → extract debt with pdfplumber
"""

from __future__ import annotations
import re
import io
from datetime import datetime
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent


BASE_URL = "https://pro.mcohio.org"


def parse_montgomery_case_number(raw: str) -> Optional[dict]:
    """
    Parse a Montgomery County case number into search components.

    Accepts variations:
      '2025 CV 02948-1 (0)'  -> search_text='2025 CV 02948'
      '2025 CV 02948 (0)'    -> search_text='2025 CV 02948'
      '2023 CV 06001 (0)'    -> search_text='2023 CV 06001'
      '2025CV02948'           -> search_text='2025 CV 02948'

    Returns None if not parseable.
    """
    if not raw:
        return None

    # Strip auction suffix like " (0)" or " (12345)"
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())
    # Strip parcel suffix like "-1", "-2" at end
    cleaned = re.sub(r"-\d+$", "", cleaned.strip())

    # Try with spaces: "2025 CV 02948"
    m = re.match(r"^(\d{4})\s*(CV)\s*(\d+)$", cleaned.strip(), re.IGNORECASE)
    if m:
        year = int(m.group(1))
        prefix = m.group(2).upper()
        number = m.group(3)
        # Montgomery format uses spaces: "YYYY CV NNNNN"
        search_text = f"{year} {prefix} {number.zfill(5)}"
        return {
            "year": year,
            "prefix": prefix,
            "number": number,
            "search_text": search_text,
        }

    return None


def extract_debt_from_pdf_bytes(pdf_bytes: bytes) -> Optional[float]:
    """
    Extract the judgment/debt amount from a foreclosure judgment PDF.
    Same logic as Franklin scraper — scan for dollar amounts near
    judgment keywords and return the largest qualifying amount.
    """
    try:
        import pdfplumber
    except ImportError:
        print("      ⚠ pdfplumber not installed — cannot parse PDF")
        return None

    amounts = []
    full_text = ""

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:10]:
                page_text = page.extract_text() or ""
                full_text += page_text + "\n"
    except Exception as e:
        print(f"      ⚠ PDF parse error: {e}")
        return None

    if not full_text.strip():
        return None

    text_lower = full_text.lower()
    judgment_keywords = [
        "judgment", "decree", "amount due", "principal",
        "total amount", "sum of", "awarded", "ordered to pay",
        "indebtedness", "balance due", "amount owing",
    ]

    dollar_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
    for match in dollar_pattern.finditer(full_text):
        try:
            amt = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if amt < 1000:
            continue
        start = max(0, match.start() - 500)
        context = text_lower[start:match.end()]
        if any(kw in context for kw in judgment_keywords):
            amounts.append(amt)

    if not amounts:
        for match in dollar_pattern.finditer(full_text):
            try:
                amt = float(match.group(1).replace(",", ""))
                if amt > 10000:
                    amounts.append(amt)
            except ValueError:
                continue

    return max(amounts) if amounts else None


class MontgomeryDocketScraper(DocketScraper):

    county_id = "montgomery-oh"
    county_name = "Montgomery"

    async def scrape_case(self, case_number: str) -> DocketResult:
        """Run the full scrape against one Montgomery County case."""
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
        )

        parsed = parse_montgomery_case_number(case_number)
        if not parsed:
            result.classification = "unknown"
            result.classification_reason = f"case number not parseable: {case_number}"
            return result

        diag_dir = Path("data/diagnostics/montgomery-oh")
        diag_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1400, "height": 900},
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await context.new_page()

            async def snap(label):
                try:
                    ts = datetime.now().strftime("%H%M%S")
                    await page.screenshot(
                        path=str(diag_dir / f"{ts}-{label}.png"),
                        full_page=True,
                    )
                    print(f"      📸 {label}: {page.url}")
                except Exception as e:
                    print(f"      ⚠ snap failed: {e}")

            async def dump(label, selector="body", inline_chars=2500):
                """Save container outerHTML to disk and log a truncated copy."""
                try:
                    el = page.locator(selector).first
                    if await el.count() == 0:
                        print(f"      ⚠ dump {label}: selector '{selector}' not present")
                        return
                    html = await el.evaluate("el => el.outerHTML")
                    ts = datetime.now().strftime("%H%M%S")
                    path = diag_dir / f"{ts}-{label}.html"
                    path.write_text(html, encoding="utf-8")
                    snippet = html.replace("\n", " ")[:inline_chars]
                    print(f"      🧾 dump {label} ({selector}) bytes={len(html)} → {path.name}")
                    print(f"         {snippet}")
                except Exception as e:
                    print(f"      ⚠ dump {label} failed: {e}")
            self._dump = dump

            try:
                # ─── Step 1: Load portal and accept disclaimer ───
                print(f"      ▶ step 1: load portal & accept disclaimer")
                await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                await snap("01_landing")

                await self._accept_disclaimer(page)
                await snap("02_after_disclaimer")

                # ─── Step 2: Navigate to search ───
                print(f"      ▶ step 2: navigate to search")
                await self._navigate_to_search(page)
                await snap("03_search_page")

                # ─── Step 3: Search for the case ───
                print(f"      ▶ step 3: search for case '{parsed['search_text']}'")
                found = await self._search_case(page, parsed)
                await snap("04_after_search")
                print(f"      → landed at: {page.url}")
                print(f"      → search found case? {found}")

                if not found:
                    result.classification = "unknown"
                    result.classification_reason = (
                        f"search did not find case. URL: {page.url}"
                    )
                    return result

                result.case_url = page.url

                # ─── Step 4: Scrape case summary ───
                print(f"      ▶ step 4: scrape case summary")
                await self._scrape_summary(page, result)
                await snap("05_case_summary")
                print(f"      → title: {result.case_title[:60] if result.case_title else 'N/A'}")

                # ─── Step 5: Scrape docket entries + find judgment PDF ───
                print(f"      ▶ step 5: scrape docket entries")
                await self._scrape_docket(page, result)
                await snap("06_docket")
                print(f"      → events={len(result.events)}, kill={result.kill_signals}")
                print(f"      → prayer_amount=${result.prayer_amount:,.0f} (source: {result.debt_source or 'none'})")

                # ─── Step 6: Scrape parties ───
                print(f"      ▶ step 6: scrape parties")
                await self._scrape_parties(page, result)
                await snap("07_parties")
                print(f"      → defendants={len(result.defendants)}")

                # ─── Step 7: Classify ───
                result.classification, result.classification_reason = self.classify(result, 0.0)
                print(f"      → classification: {result.classification} ({result.classification_reason})")

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

    # ─── Step 1: Accept Disclaimer ───────────────────────────────────────

    async def _accept_disclaimer(self, page: Page) -> None:
        """
        Montgomery PRO has a disclaimer modal triggered by javascript:openDisclaimer().
        It may also show as a blocking page on first visit.
        Try multiple approaches to accept it.
        """
        # PRO V3 shows a disclaimer modal on page load with "I Agree" / "Disagree" buttons.
        # The modal overlays the search form, so we MUST dismiss it first.
        # The onclick is acceptDisclaimer() — try clicking the button directly.

        # First try the "I Agree" button (confirmed from diagnostics)
        for sel in [
            "button:has-text('I Agree')",
            "button:has-text('Accept')",
            "button:has-text('Agree')",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=5000)
                    await page.wait_for_timeout(2000)
                    print(f"      → accepted disclaimer: {sel}")
                    return
            except Exception:
                continue

        # Fallback: call acceptDisclaimer() directly via JS
        try:
            await page.evaluate("if(typeof acceptDisclaimer === 'function') acceptDisclaimer()")
            await page.wait_for_timeout(2000)
            print(f"      → accepted disclaimer via JS: acceptDisclaimer()")
            return
        except Exception:
            pass

        print(f"      ⚠ disclaimer acceptance uncertain, continuing...")

    # ─── Step 2: Navigate to Search ──────────────────────────────────────

    async def _navigate_to_search(self, page: Page) -> None:
        """
        PRO V3 is a single-page app with fragment-based navigation.
        Search = #Home, Results = #Results, Case Info = #Case.
        The search form is on the landing page itself — just click
        the #Home anchor to ensure it's showing.
        """
        # PRO V3 uses fragment anchors — click #Home to show search section
        for sel in [
            "a[href='#Home']",
            "a[href='#Search']",
            "a:has-text('Search')",
        ]:
            try:
                link = page.locator(sel).first
                if await link.count() > 0 and await link.is_visible():
                    await link.click(timeout=3000)
                    await page.wait_for_timeout(1500)
                    print(f"      → clicked SPA nav: {sel}")
                    break
            except Exception:
                continue

        # Verify the search section is visible — look for input fields
        await page.wait_for_timeout(1000)
        body = (await page.inner_text("body")).lower()
        if "case" in body or "search" in body or "number" in body:
            print(f"      → search section visible on SPA")
        else:
            print(f"      ⚠ search section may not be visible")

    # ─── Step 3: Search for a case ───────────────────────────────────────

    async def _search_case(self, page: Page, parsed: dict) -> bool:
        """Fill the case number search field and submit."""
        search_text = parsed["search_text"]
        await page.wait_for_timeout(1000)

        # Dump current page state for diagnostics
        body = await page.inner_text("body")
        body_lower = body.lower()
        print(f"      → page URL: {page.url}")
        print(f"      → page has 'case': {'case' in body_lower}, 'search': {'search' in body_lower}")
        print(f"      → page text (first 500): {body[:500].strip()}")

        # List all visible input fields for diagnostics
        inputs = await page.query_selector_all("input")
        for inp in inputs[:10]:
            try:
                inp_id = await inp.get_attribute("id") or ""
                inp_name = await inp.get_attribute("name") or ""
                inp_type = await inp.get_attribute("type") or ""
                inp_ph = await inp.get_attribute("placeholder") or ""
                visible = await inp.is_visible()
                print(f"      → input: id='{inp_id}' name='{inp_name}' type='{inp_type}' placeholder='{inp_ph}' visible={visible}")
            except Exception:
                pass

        # Try to find a case number input field
        input_filled = False
        for sel in [
            "input[id*='CaseNumber' i]",
            "input[name*='CaseNumber' i]",
            "input[id*='caseNum' i]",
            "input[name*='caseNum' i]",
            "input[id*='txtCase' i]",
            "input[name*='txtCase' i]",
            "input[id*='case' i]",
            "input[name*='case' i]",
            "input[id*='search' i]",
            "input[placeholder*='case' i]",
            "input[placeholder*='search' i]",
            "input[type='text']",
            "input[type='search']",
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.fill(search_text, timeout=3000)
                    input_filled = True
                    print(f"      → filled search input: {sel} with '{search_text}'")
                    break
            except Exception:
                continue

        if not input_filled:
            print(f"      ⚠ could not find search input")
            return False

        await page.wait_for_timeout(500)

        # Dump all buttons for diagnostics
        buttons = await page.query_selector_all("button, input[type='submit'], a.btn, a.button")
        for btn_el in buttons[:15]:
            try:
                btn_text = (await btn_el.inner_text()).strip()[:60]
                btn_id = await btn_el.get_attribute("id") or ""
                btn_class = await btn_el.get_attribute("class") or ""
                btn_tag = await btn_el.evaluate("el => el.tagName.toLowerCase()")
                btn_visible = await btn_el.is_visible()
                btn_onclick = await btn_el.get_attribute("onclick") or ""
                print(f"      → button: tag={btn_tag} id='{btn_id}' class='{btn_class[:40]}' text='{btn_text}' visible={btn_visible} onclick='{btn_onclick[:60]}'")
            except Exception:
                pass

        # The search button onclick is: openTab('genSearch',$('#frmGenSearch').serialize())
        # Try clicking the visible btn-success Search button first, then JS fallback
        submitted = False

        # The visible Search button is: button.btn.btn-success with onclick containing genSearch
        try:
            search_btns = page.locator("button.btn-success:visible")
            count = await search_btns.count()
            if count > 0:
                await search_btns.first.click(timeout=5000)
                submitted = True
                print(f"      → clicked btn-success Search button")
        except Exception as e:
            print(f"      ⚠ btn-success click failed: {e}")

        if not submitted:
            # Fallback: call the JS function directly
            try:
                await page.evaluate("openTab('genSearch',$('#frmGenSearch').serialize())")
                submitted = True
                print(f"      → called openTab() via JS directly")
            except Exception as e:
                print(f"      ⚠ JS openTab() failed: {e}")

        if not submitted:
            await page.keyboard.press("Enter")
            print(f"      → pressed Enter as last resort")

        # SPA: openTab() fires an AJAX call that populates the #Results section.
        # Wait for the response, then look for results content.
        await page.wait_for_timeout(5000)

        # The SPA has sections: #Home (search), #Results, #Case, etc.
        # After search, #Results should now have content. Click it.
        try:
            await page.click("a[href='#Results']", timeout=3000)
            await page.wait_for_timeout(2000)
            print(f"      → clicked #Results nav")
        except Exception:
            print(f"      ⚠ could not click #Results nav")

        # Check what's in the Results section — look for a results div/table
        # The PRO V3 SPA likely has a div#Results or similar
        results_text = ""
        for sel in ["#Results", "#results", "[id*='Results']", "[id*='results']", "#tblResults", ".results-table"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    results_text = await el.inner_text()
                    if results_text.strip():
                        print(f"      → results container ({sel}) text (first 300): {results_text[:300].strip()}")
                        break
            except Exception:
                continue

        if not results_text.strip():
            # Fallback: dump visible text to see what's showing
            body = await page.inner_text("body")
            print(f"      → no results container found. Full page text (first 800): {body[:800].strip()}")

        # Dump the first results row so we can see what's actually clickable.
        # PRO V3 renders the case number cell as an <a> with an onclick that
        # invokes openTab('Case', ...) — clicking the <tr> itself does nothing.
        await self._dump("results_first_row", "#Results table tbody tr:first-child", inline_chars=1500)

        row_anchors = await page.query_selector_all("#Results table tbody tr:first-child a")
        print(f"      → first row anchors: {len(row_anchors)}")
        for a in row_anchors[:8]:
            try:
                a_text = (await a.inner_text()).strip()[:60]
                a_href = await a.get_attribute("href") or ""
                a_onclick = await a.get_attribute("onclick") or ""
                print(f"         · a text='{a_text}' href='{a_href[:60]}' onclick='{a_onclick[:80]}'")
            except Exception:
                pass

        # Strategy: prefer clicking the case-number anchor inside the first row.
        # Fall back to any anchor in the row, then to direct JS invocation of
        # the row's onclick if Playwright misroutes the click.
        clicked_case = False
        for link_sel in [
            f"#Results table tbody tr:first-child a:has-text('{parsed['search_text']}')",
            f"#Results table tbody tr:first-child a:has-text('{parsed['year']} CV')",
            "#Results table tbody tr:first-child a[onclick*='Case' i]",
            "#Results table tbody tr:first-child a[onclick*='openTab' i]",
            "#Results table tbody tr:first-child td:first-child a",
            "#Results table tbody tr:first-child a",
        ]:
            try:
                link = page.locator(link_sel).first
                if await link.count() > 0:
                    await link.scroll_into_view_if_needed(timeout=2000)
                    await link.click(timeout=5000, force=True)
                    await page.wait_for_timeout(3500)
                    clicked_case = True
                    print(f"      → clicked case anchor: {link_sel}")
                    break
            except Exception as e:
                print(f"      ⚠ click {link_sel} failed: {type(e).__name__}: {e}")
                continue

        if not clicked_case:
            # Last resort: synthesize the click via JS on the first row's first link.
            try:
                await page.evaluate(
                    "const a = document.querySelector('#Results table tbody tr a');"
                    " if (a) { a.click(); }"
                )
                await page.wait_for_timeout(3500)
                clicked_case = True
                print(f"      → triggered first-row anchor.click() via JS")
            except Exception as e:
                print(f"      ⚠ JS row click failed: {e}")

        # PRO V3 tab switching is independent of data loading. Explicitly click
        # the #Case nav so the SPA reveals whatever the row click populated.
        try:
            case_nav = page.locator("a[href='#Case']").first
            if await case_nav.count() > 0:
                await case_nav.click(timeout=3000, force=True)
                await page.wait_for_timeout(2000)
                print(f"      → clicked #Case tab nav")
        except Exception as e:
            print(f"      ⚠ #Case tab nav failed: {e}")

        await self._dump("case_section", "#Case", inline_chars=3000)

        # Compute case_text strictly from the #Case container; falling back
        # to body would re-introduce the search-results false positive.
        case_text = ""
        try:
            case_el = page.locator("#Case").first
            if await case_el.count() > 0:
                case_text = await case_el.inner_text()
                print(f"      → #Case inner_text len={len(case_text)} (first 400): {case_text[:400].strip()}")
        except Exception as e:
            print(f"      ⚠ #Case inner_text failed: {e}")

        if not case_text.strip():
            await self._dump("body_after_click", "body", inline_chars=2000)

        case_text_lower = case_text.lower()
        has_case = any(kw in case_text_lower for kw in [
            "docket", "filing date", "plaintiff", "defendant",
            "case status", "case type", "judge",
        ])
        return has_case

    # ─── Step 4: Scrape case summary ─────────────────────────────────────

    async def _scrape_summary(self, page: Page, result: DocketResult) -> None:
        """Extract case title, filing date, status from the #Case SPA section."""
        text = ""
        try:
            case_el = page.locator("#Case").first
            if await case_el.count() > 0:
                text = await case_el.inner_text()
        except Exception:
            text = ""
        if not text.strip():
            print(f"      ⚠ summary: #Case empty, falling back to body")
            text = await page.inner_text("body")

        # Case title — look for "vs" pattern
        title_m = re.search(
            r"([A-Z][A-Z\s,\.&\']+(?:VS\.?|V\.)\s+[A-Z][A-Z\s,\.&\']+)",
            text,
        )
        if title_m:
            result.case_title = title_m.group(1).strip().replace("\n", " ")[:200]

        # Filing date
        fd_m = re.search(r"(?:Filing|Filed)\s*(?:Date)?\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
        if fd_m:
            try:
                mm, dd, yyyy = fd_m.group(1).split("/")
                result.filing_date = f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
            except ValueError:
                pass

        # Case status
        status_m = re.search(r"(?:Case\s+)?Status\s*:?\s*([A-Z][A-Za-z /]+)", text)
        if status_m:
            result.last_status = status_m.group(1).strip()[:50]

        # Case type
        type_m = re.search(r"(?:Case\s+)?Type\s*:?\s*([A-Z][A-Za-z /\-]+)", text)
        if type_m:
            result.case_designation = type_m.group(1).strip()[:80]

        # Scan for kill signals and proof
        result.kill_signals = self.detect_kill_signals(text)
        proof = self.detect_proof_of_surplus(text)
        if proof:
            result.proof_of_surplus = proof

    # ─── Step 5: Scrape docket + judgment PDF ────────────────────────────

    async def _scrape_docket(self, page: Page, result: DocketResult) -> None:
        """Read docket entries from the #Case SPA section, find judgment PDFs.

        PRO V3 renders the docket inline inside the #Case tab — there is no
        separate Docket page to navigate to. Trying to click a Docket link
        elsewhere just bounces the user back to the search form.
        """
        # Make sure #Case is the active tab.
        try:
            case_nav = page.locator("a[href='#Case']").first
            if await case_nav.count() > 0:
                await case_nav.click(timeout=3000, force=True)
                await page.wait_for_timeout(1500)
        except Exception:
            pass

        case_locator = page.locator("#Case").first
        case_exists = await case_locator.count() > 0
        docket_text = ""
        if case_exists:
            try:
                docket_text = await case_locator.inner_text()
            except Exception:
                docket_text = ""
        if not docket_text.strip():
            print(f"      ⚠ docket: #Case empty, falling back to body")
            docket_text = await page.inner_text("body")

        await self._dump("case_for_docket", "#Case", inline_chars=4000)

        # Kill signals + proof + competing filers
        result.kill_signals = list(set(
            result.kill_signals + self.detect_kill_signals(docket_text)
        ))
        result.competing_filers = self.detect_competing_filers(docket_text)
        proof = self.detect_proof_of_surplus(docket_text)
        if proof and not result.proof_of_surplus:
            result.proof_of_surplus = proof

        if re.search(r"owner'?s?\s+claim", docket_text, re.IGNORECASE):
            result.owner_filed_claim = True

        # Extract docket events from rows inside #Case only (not page-wide
        # tables, which include the search-results table from the Results tab).
        events = []
        if case_exists:
            rows = await case_locator.locator("table tr, tr").element_handles()
        else:
            rows = await page.query_selector_all("table tr, tr")
        for row in rows:
            row_text = (await row.inner_text()).strip()
            if not row_text:
                continue
            m = re.match(r"^(\d{1,2}/\d{1,2}/\d{4})\s+(.+)", row_text, re.DOTALL)
            if m:
                try:
                    mm, dd, yyyy = m.group(1).split("/")
                    events.append(DocketEvent(
                        filing_date=f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}",
                        description=m.group(2).strip()[:200],
                    ))
                except ValueError:
                    pass
        result.events = [e.__dict__ for e in events[:50]]

        if events:
            sorted_events = sorted(events, key=lambda e: e.filing_date, reverse=True)
            result.last_activity_date = sorted_events[0].filing_date

        # Try to extract debt from judgment PDF
        await self._extract_judgment_from_pdf(page, result)

    async def _extract_judgment_from_pdf(self, page: Page, result: DocketResult) -> None:
        """Find judgment-related links in the #Case section, extract debt."""
        if result.prayer_amount > 0:
            return

        case_locator = page.locator("#Case").first
        if await case_locator.count() > 0:
            links = await case_locator.locator("a").element_handles()
        else:
            links = await page.query_selector_all("a")
        print(f"      → scanning {len(links)} anchors in #Case for judgment links")
        judgment_links = []

        for link in links:
            try:
                text = (await link.inner_text()).strip().lower()
                href = await link.get_attribute("href") or ""
            except Exception:
                continue

            judgment_keywords = [
                "judgment", "decree", "summary judgment",
                "default judgment", "foreclosure judgment",
                "final judgment", "entry of judgment",
                "magistrate decision",
            ]
            is_judgment = any(kw in text for kw in judgment_keywords)
            is_doc_link = any(ext in href.lower() for ext in [".pdf", "document", "doc", "image", "view"])

            if is_judgment or (is_doc_link and any(kw in text for kw in judgment_keywords)):
                judgment_links.append((link, text, href))

        if not judgment_links:
            print(f"      → no judgment PDF links found in docket")
            self._extract_debt_from_text(await page.inner_text("body"), result)
            return

        for link_el, link_text, href in judgment_links[:3]:
            print(f"      → trying judgment link: '{link_text[:60]}' → {href[:80]}")
            try:
                async with page.expect_download(timeout=15000) as download_info:
                    await link_el.click()
                download = await download_info.value
                tmp_path = await download.path()
                if tmp_path:
                    pdf_bytes = Path(tmp_path).read_bytes()
                    amount = extract_debt_from_pdf_bytes(pdf_bytes)
                    if amount:
                        result.prayer_amount = amount
                        result.debt_source = "pdf_extract"
                        print(f"      ✅ extracted debt from PDF: ${amount:,.2f}")
                        return
                    else:
                        print(f"      → no debt amount found in PDF")
            except Exception:
                pass

            # Fallback: navigate to href and read content
            try:
                original_url = page.url
                full_href = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
                response = await page.goto(full_href, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)

                content_type = response.headers.get("content-type", "") if response else ""
                if "pdf" in content_type:
                    body = await response.body()
                    amount = extract_debt_from_pdf_bytes(body)
                    if amount:
                        result.prayer_amount = amount
                        result.debt_source = "pdf_extract"
                        print(f"      ✅ extracted debt from direct PDF: ${amount:,.2f}")
                        await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                        return
                else:
                    viewer_text = await page.inner_text("body")
                    self._extract_debt_from_text(viewer_text, result)
                    if result.prayer_amount > 0:
                        print(f"      ✅ extracted debt from doc viewer: ${result.prayer_amount:,.2f}")
                        await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                        return

                await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)
            except Exception as e:
                print(f"      ⚠ link follow failed: {e}")
                continue

        if result.prayer_amount == 0:
            self._extract_debt_from_text(await page.inner_text("body"), result)

    def _extract_debt_from_text(self, text: str, result: DocketResult) -> None:
        """Extract debt from inline docket text near judgment keywords."""
        if result.prayer_amount > 0:
            return

        text_lower = text.lower()
        judgment_keywords = [
            "judgment", "decree", "amount due", "principal",
            "total amount", "sum of", "awarded", "indebtedness",
        ]

        dollar_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
        amounts = []

        for match in dollar_pattern.finditer(text):
            try:
                amt = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if amt < 1000:
                continue
            start = max(0, match.start() - 500)
            context = text_lower[start:match.end()]
            if any(kw in context for kw in judgment_keywords):
                amounts.append(amt)

        if amounts:
            result.prayer_amount = max(amounts)
            result.debt_source = "docket_text_extract"

    # ─── Step 6: Scrape parties ──────────────────────────────────────────

    async def _scrape_parties(self, page: Page, result: DocketResult) -> None:
        """Extract plaintiff and defendants."""
        for sel in [
            "a:has-text('Parties')",
            "a:has-text('Party')",
            "a[href*='parties' i]",
            "a[href*='party' i]",
        ]:
            try:
                link = page.locator(sel).first
                if await link.count() > 0:
                    await link.click(timeout=5000)
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await page.wait_for_timeout(1500)
                    break
            except Exception:
                continue

        text = await page.inner_text("body")

        p_m = re.search(r"PLAINTIFF\s*:?\s*\n?\s*([^\n]+)", text, re.IGNORECASE)
        if p_m:
            result.plaintiff = p_m.group(1).strip()[:200]

        defendants = []
        for m in re.finditer(r"DEFENDANT\s*:?\s*\n?\s*([^\n]+)", text, re.IGNORECASE):
            name = m.group(1).strip()[:200]
            if name and name not in defendants:
                defendants.append(name)
        result.defendants = defendants

        creditor_keywords = [
            "LLC", "BANK", "TREASURER", "IRS", "STATE OF", "COUNTY",
            "CITY OF", "REVENUE", "DEPARTMENT", "ASSOCIATION", "TRUST",
            "FINANCIAL", "MORTGAGE", "CAPITAL", "FUND", "SERVICES", "INC",
        ]
        for name in defendants:
            name_upper = name.upper()
            if any(kw in name_upper for kw in creditor_keywords):
                result.additional_parties.append(name)
