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


def extract_debt_from_pdf_bytes(pdf_bytes: bytes) -> tuple[Optional[float], str]:
    """Extract the judgment/debt amount from a foreclosure judgment PDF.

    Returns (amount, snippet) — snippet is ~400 chars of PDF text centered on
    the chosen dollar match, so the caller can prove the number came from a
    judgment line and not a fee/cost/interest line.
    """
    try:
        import pdfplumber
    except ImportError:
        print("      ⚠ pdfplumber not installed — cannot parse PDF")
        return None, ""

    full_text = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:10]:
                page_text = page.extract_text() or ""
                full_text += page_text + "\n"
    except Exception as e:
        print(f"      ⚠ PDF parse error: {e}")
        return None, ""

    if not full_text.strip():
        return None, ""

    text_lower = full_text.lower()
    judgment_keywords = [
        "judgment", "decree", "amount due", "principal",
        "total amount", "sum of", "awarded", "ordered to pay",
        "indebtedness", "balance due", "amount owing",
    ]

    dollar_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
    qualified = []  # (amount, match_start, match_end)
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
            qualified.append((amt, match.start(), match.end()))

    if qualified:
        best = max(qualified, key=lambda x: x[0])
        amt, ms, me = best
    else:
        # Fallback: largest > $10K anywhere in the PDF
        loose = []
        for match in dollar_pattern.finditer(full_text):
            try:
                a = float(match.group(1).replace(",", ""))
                if a > 10000:
                    loose.append((a, match.start(), match.end()))
            except ValueError:
                continue
        if not loose:
            return None, ""
        best = max(loose, key=lambda x: x[0])
        amt, ms, me = best

    snip_start = max(0, ms - 200)
    snip_end = min(len(full_text), me + 200)
    snippet = full_text[snip_start:snip_end].replace("\n", " ⏎ ").strip()
    return amt, snippet


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

        # SPA: openTab() fires an AJAX call that populates the #Results
        # section. Per CLAUDE.md (placeholder-container trap, FP-12 Stage 1
        # lesson): we must wait for ACTUAL POPULATED ROWS inside
        # #tblSearchResults, not just for the wrapper to attach — the
        # wrapper exists before openTab returns. wait_for_function counts
        # rows directly; safe because it bypasses the placeholder trap
        # that broke FL leads in Stage 1.
        try:
            await page.wait_for_function(
                "() => { const t = document.querySelector('#tblSearchResults'); "
                "return t && t.querySelectorAll('tr').length > 0; }",
                timeout=8000,
            )
            print(f"      → #tblSearchResults populated")
        except PWTimeout:
            print(f"      ⚠ #tblSearchResults did not populate within 8s — "
                  f"falling back to existing post-click logic")

        # The SPA has sections: #Home (search), #Results, #Case, etc.
        # After search, #Results should now have content. Click it.
        try:
            await page.click("a[href='#Results']", timeout=3000)
            # No blind sleep — #tblSearchResults is already populated
            # (wait_for_function above) or the timeout fell through. The
            # next operation (row dump) tolerates an empty table.
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

        # The PRO V3 results table is #tblSearchResults — NOT the outer table
        # that wraps the search-parameters header. Each row is
        #   <tr style="cursor:pointer;"
        #       onclick="openTab('caseInfo','case_id=NNNN&screen=summary',1,'YYYY CV NNNNN');">
        # All rows for one searched case number share the same case_id, so
        # picking the first is fine.
        await self._dump("results_first_row", "#tblSearchResults tr:first-child", inline_chars=1500)

        first_row_onclick = ""
        try:
            first_row = page.locator("#tblSearchResults tr:first-child").first
            if await first_row.count() > 0:
                first_row_onclick = await first_row.get_attribute("onclick") or ""
                print(f"      → first row onclick: {first_row_onclick[:160]}")
        except Exception as e:
            print(f"      ⚠ first row onclick read failed: {e}")

        # Parse the case_id out of the onclick so we can build PDF URLs directly:
        #   openTab('caseInfo','case_id=NNNN&screen=summary',1,'YYYY CV NNNNN');
        m_case_id = re.search(r"case_id=(\d+)", first_row_onclick or "")
        if m_case_id:
            self._active_case_id = m_case_id.group(1)
            print(f"      → captured case_id={self._active_case_id}")
        else:
            self._active_case_id = ""

        clicked_case = False
        try:
            row = page.locator("#tblSearchResults tr:first-child").first
            if await row.count() > 0:
                await row.scroll_into_view_if_needed(timeout=2000)
                await row.click(timeout=5000, force=True)
                clicked_case = True
                print(f"      → clicked #tblSearchResults tr:first-child")
        except Exception as e:
            print(f"      ⚠ row click failed: {type(e).__name__}: {e}")

        if not clicked_case and first_row_onclick:
            # Fallback: invoke the row's onclick attribute as JS directly so
            # we sidestep any element-targeting issues.
            try:
                await page.evaluate(f"(function(){{ {first_row_onclick} }})()")
                clicked_case = True
                print(f"      → invoked first-row onclick via JS eval")
            except Exception as e:
                print(f"      ⚠ JS onclick eval failed: {e}")

        # openTab() does an AJAX fetch then injects the case detail into #Case.
        # Poll for #Case to fill so we don't proceed before the SPA is ready.
        case_loaded = False
        for _ in range(20):  # ~10s budget
            await page.wait_for_timeout(500)
            try:
                ln = await page.locator("#Case").first.evaluate("el => el.innerText.length")
            except Exception:
                ln = 0
            if ln and ln > 80:
                case_loaded = True
                break
        print(f"      → #Case populated wait: loaded={case_loaded}")

        # Make sure #Case is the visible tab once it's populated.
        try:
            case_nav = page.locator("a[href='#Case']").first
            if await case_nav.count() > 0:
                await case_nav.click(timeout=3000, force=True)
                await page.wait_for_timeout(1500)
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

    # ─── #Case subscreen switcher ────────────────────────────────────────

    async def _switch_case_subscreen(self, page: Page, name: str) -> bool:
        """Click the #menu_CI_<Name> tab inside #Case and wait for refresh.

        PRO V3 uses <span> elements (not <a>) for the case sub-nav. Clicking
        re-runs openTab('caseInfo', '...&screen=<name>...') which replaces
        the inner HTML of #Case. We snapshot a content hash before clicking
        and poll until it changes (or 10s elapses).
        """
        sel = f"#menu_CI_{name}"
        try:
            menu_el = page.locator(sel).first
            if await menu_el.count() == 0:
                print(f"      ⚠ subscreen menu '{name}' not present")
                return False

            try:
                before_len = await page.locator("#Case").first.evaluate(
                    "el => el.innerText.length"
                )
            except Exception:
                before_len = 0

            await menu_el.scroll_into_view_if_needed(timeout=2000)
            await menu_el.click(timeout=5000, force=True)
            print(f"      → switched #Case subscreen → {name}")

            for _ in range(20):  # ~10s budget
                await page.wait_for_timeout(500)
                try:
                    cur_len = await page.locator("#Case").first.evaluate(
                        "el => el.innerText.length"
                    )
                except Exception:
                    cur_len = before_len
                if cur_len != before_len and cur_len > 80:
                    return True
            print(f"      ⚠ subscreen '{name}' content did not change after click")
            return False
        except Exception as e:
            print(f"      ⚠ subscreen switch to '{name}' failed: {e}")
            return False

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

        # Case title — PRO V3 renders it as
        # "YYYY CV NNNNN: PLAINTIFF NAME vs DEFENDANT NAME"
        # both inline in the Summary screen and inside the print button header.
        title_m = re.search(
            r"\b(\d{4}\s+CV\s+\d+):\s*([A-Z][A-Z0-9 ,.&'/\-]{3,}\s+vs\.?\s+[A-Z][A-Z0-9 ,.&'/\-]{3,})",
            text,
            re.IGNORECASE,
        )
        if title_m:
            result.case_title = f"{title_m.group(1)}: {title_m.group(2)}".strip()[:200]
        else:
            # Fallback: classic "PLAINTIFF vs DEFENDANT" only.
            alt = re.search(
                r"([A-Z][A-Z0-9 ,.&'/\-]{3,}\s+(?:vs\.?|v\.)\s+[A-Z][A-Z0-9 ,.&'/\-]{3,})",
                text,
            )
            if alt:
                result.case_title = alt.group(1).strip().replace("\n", " ")[:200]

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
        """Read docket entries from the #Case Docket subscreen, find judgment PDFs.

        PRO V3 keeps a sub-nav inside #Case (#menu_CI_Summary, #menu_CI_Docket,
        #menu_CI_Party, ...). The default load after a row click is the Summary
        screen — to see docket entries we must click #menu_CI_Docket, which
        re-issues openTab('caseInfo','...&screen=docket&sort=A...').
        """
        await self._switch_case_subscreen(page, "Docket")

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
        """Walk #tblDocketBody, pull judgment docket rows, fetch + parse the PDFs.

        PRO V3 wires docket PDFs through DOWNLOAD <button>s whose onclick is
        window.open('/Helpers/getDocumentFromOnBase.aspx?docketid=NN&caseid=MM&documenttype=docket').
        We rebuild that URL from the row id (id="docket_row_NN") and the
        case_id captured from the result-row onclick, then fetch via
        page.context.request to preserve cookies/session.
        """
        if result.prayer_amount > 0:
            return

        case_id = getattr(self, "_active_case_id", "")
        rows = await page.query_selector_all("#tblDocketBody td.docketRows")
        print(f"      → docket rows found: {len(rows)} (case_id={case_id or 'unknown'})")

        # JUDGMENT requires one of these doc phrases (most-authoritative first).
        # "judgment" alone is NOT sufficient — that catches clerk fees, lien
        # releases, etc. The phrase must name an actual judgment instrument.
        judgment_doc_phrases = [
            "judgment entry and foreclosure decree",
            "judgment and decree of foreclosure",
            "judgment entry of foreclosure",
            "judgment and decree",
            "decree of foreclosure",
            "decree in foreclosure",
            "foreclosure decree",
            "final judgment entry",
            "final judgment",
            "final entry",
            "judgment entry",
            "entry of judgment",
            "magistrate's decision",
            "magistrate decision",
        ]
        # Hard exclusions — any of these in the row text disqualifies it.
        # Fees, costs, clerk paperwork, lien releases, and judgment-debt
        # satisfactions look judgment-adjacent but are not the judgment.
        exclusion_markers = [
            "fee", "cost statement", "clerk fee", "deposit", "refund",
            "release of judgment", "release of lien", "release of liens",
            "partial release", "satisfaction of judgment", "satisfaction",
            "transcript", "subpoena", "praecipe", "writ of execution",
            "garnishment",
        ]
        # Supporting filings — logged for trail but never fetched. A motion
        # or affidavit can quote the prayer but is not the order itself.
        supporting_doc_markers = [
            "motion:", "motion for", "affidavit", "notice:", "notice of filing",
            "memorandum", "brief in", "reply in support", "exhibit",
        ]

        judgment_candidates = []  # (prio, docket_id, preview)
        supporting_seen = []      # (prio, docket_id, preview) — for log only
        excluded_count = 0

        for row in rows:
            try:
                row_id = await row.get_attribute("id") or ""
                m = re.match(r"docket_row_(\d+)", row_id)
                if not m:
                    continue
                docket_id = m.group(1)
                row_text = (await row.inner_text()).strip()
                tl = row_text.lower()
                preview = row_text[:160].replace("\n", " ")

                if any(ex in tl for ex in exclusion_markers):
                    excluded_count += 1
                    continue

                matched_prio = None
                for prio, phrase in enumerate(judgment_doc_phrases):
                    if phrase in tl:
                        matched_prio = prio
                        break

                if matched_prio is None:
                    continue

                if any(sup in tl for sup in supporting_doc_markers):
                    supporting_seen.append((matched_prio, docket_id, preview))
                else:
                    judgment_candidates.append((matched_prio, docket_id, preview))
            except Exception:
                continue

        judgment_candidates.sort(key=lambda c: c[0])
        supporting_seen.sort(key=lambda c: c[0])

        print(
            f"      → judgment candidates: {len(judgment_candidates)} "
            f"(supporting only: {len(supporting_seen)}, excluded fee/clerk/release: {excluded_count})"
        )
        for prio, docket_id, preview in judgment_candidates[:6]:
            print(f"         · [JUDGMENT] prio={prio} docketid={docket_id} :: {preview}")
        for prio, docket_id, preview in supporting_seen[:4]:
            print(f"         · [SUPPORTING — not fetched] prio={prio} docketid={docket_id} :: {preview}")

        if not judgment_candidates:
            print(f"      → no qualified judgment document; prayer stays $0 (lead → unknown)")
            return

        if not case_id:
            print(f"      ⚠ cannot fetch PDFs without case_id; prayer stays $0 (lead → unknown)")
            return

        req = page.context.request
        for prio, docket_id, preview in judgment_candidates[:6]:
            url = (
                f"{BASE_URL}/Helpers/getDocumentFromOnBase.aspx"
                f"?docketid={docket_id}&caseid={case_id}&documenttype=docket"
            )
            try:
                resp = await req.get(url, timeout=20000)
                if not resp.ok:
                    print(f"      ⚠ PDF fetch {docket_id}: HTTP {resp.status}")
                    continue
                pdf_bytes = await resp.body()
                if not pdf_bytes or len(pdf_bytes) < 1000:
                    print(f"      ⚠ PDF {docket_id}: body too small ({len(pdf_bytes)} bytes)")
                    continue
                if pdf_bytes[:4] != b"%PDF":
                    print(f"      ⚠ PDF {docket_id}: not a PDF (first bytes {pdf_bytes[:8]!r})")
                    continue
                amount, snippet = extract_debt_from_pdf_bytes(pdf_bytes)
                if amount:
                    result.prayer_amount = amount
                    result.debt_source = f"pdf_extract:docket_{docket_id}:judgment"
                    print(f"      ✅ extracted debt from PDF {docket_id} [JUDGMENT]: ${amount:,.2f}")
                    print(f"      📄 PDF context (~400 chars around match):")
                    print(f"         {snippet}")
                    return
                else:
                    print(f"      → PDF {docket_id} parsed but no debt keyword match")
            except Exception as e:
                print(f"      ⚠ PDF fetch {docket_id} failed: {type(e).__name__}: {e}")

        # All qualified judgment PDFs were unreadable or yielded no debt-keyword
        # match. Per the anti-fabrication rule, prayer stays $0 and the lead
        # remains unknown — no text-scrape fallback.
        print(f"      → all qualified judgment PDFs failed; prayer stays $0 (lead → unknown)")
        return

    # ─── Step 6: Scrape parties ──────────────────────────────────────────

    async def _scrape_parties(self, page: Page, result: DocketResult) -> None:
        """Extract plaintiff and defendants from #tblPartyBody.

        PRO V3 renders each party as:
          <tr class="table-info"><td><div class="row"><div class="col-md-5">
            <strong>NAME</strong><br><small>TYPE</small>
          </div></div></td></tr>
        """
        await self._switch_case_subscreen(page, "Party")
        await self._dump("case_for_parties", "#Case", inline_chars=3000)

        defendants = []
        party_rows = await page.query_selector_all("#tblPartyBody tr.table-info")
        print(f"      → party rows: {len(party_rows)}")
        for row in party_rows:
            try:
                name_el = await row.query_selector("strong")
                type_el = await row.query_selector("small")
                if not name_el or not type_el:
                    continue
                name = (await name_el.inner_text()).strip()[:200]
                ptype = (await type_el.inner_text()).strip().upper()
                if not name:
                    continue
                if "PLAINTIFF" in ptype and not result.plaintiff:
                    result.plaintiff = name
                elif "DEFENDANT" in ptype:
                    if name not in defendants:
                        defendants.append(name)
            except Exception:
                continue
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
