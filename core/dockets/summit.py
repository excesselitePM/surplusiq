"""
SurplusIQ — Summit County Docket Scraper (recon-heavy first pass)

Summit's clerk portal is a mixed classic-ASP / ASP.NET WebForms site at
clerk.summitoh.net. Confirmed by recon (not the same SPA as Montgomery):

  - Entry:           /RecordsSearch/Disclaimer.asp?toPage=SelectDivision.asp
  - Disclaimer:      hyperlink "Agree" with href="SelectDivision.asp"
                     (no form submission — just a navigation link)
  - Division select: /RecordsSearch/SelectDivision.asp → "Civil" link
                     points to /PublicSite/SelectDivisionCivil.aspx
                     (transition: classic ASP → ASP.NET WebForms)
  - Search by case:  /PublicSite/SearchByCaseNbrCivil.aspx
                     SPLIT FIELDS — separate inputs for Type / Year /
                     Month / Sequence / Suffix (recon confirmed; exact
                     field IDs to be captured in the first Actions run).
  - Postbacks:       ASP.NET __doPostBack — session enforced via cookies
                     set by the Disclaimer.asp visit.
  - CAPTCHA:         config.has_captcha=True. May fire after search;
                     workflow already runs us under xvfb.
  - Case format:     CV-YYYY-MM-#### (auction raw shows CV2025020548A
                     with no dashes; we re-format into split fields).

Per Eric's six corrections + CLAUDE.md anti-fabrication rule, this
scraper MUST extract the prayer amount from the actual JUDGMENT ENTRY
PDF and must fail loudly when no qualified judgment doc is found.

This first pass is RECON-HEAVY: every page transition is screenshotted
and the container HTML is dumped to data/diagnostics/summit-oh/ so the
next Actions run surfaces the real DOM. Selectors below are educated
guesses against ASP.NET WebForms conventions and will be tightened once
the diagnostic dumps come back.
"""

from __future__ import annotations
import re
import io
from datetime import datetime
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent
from .montgomery import extract_debt_from_pdf_bytes


CLERK_HOST = "https://clerk.summitoh.net"
DISCLAIMER_URL = f"{CLERK_HOST}/RecordsSearch/Disclaimer.asp?toPage=SelectDivision.asp"
SEARCH_URL = f"{CLERK_HOST}/PublicSite/SearchByCaseNbrCivil.aspx"


def parse_summit_case_number(raw: str) -> Optional[dict]:
    """Parse Summit case number into the five split fields.

    Accepts:
      'CV2025020548A (10545)'  → type=CV year=2025 month=02 seq=0548 suffix=A
      'CV-2025-02-0548'        → suffix=''
      'CV-2025-02-0548-A'      → suffix=A
      'CV2025020548'           → suffix=''

    Returns None if not parseable. Suffix may be empty (not all cases have one).
    """
    if not raw:
        return None

    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())  # strip " (auction_id)"

    # Try dashed form first: CV-YYYY-MM-####(-SUFFIX)?
    m = re.match(
        r"^(?P<type>CV|MI|AC)-(?P<year>\d{4})-(?P<month>\d{2})-(?P<seq>\d{3,5})(?:-(?P<suffix>[A-Z]))?$",
        cleaned,
        re.IGNORECASE,
    )
    if not m:
        # Compact form: CVYYYYMMNNNN(SUFFIX)?
        m = re.match(
            r"^(?P<type>CV|MI|AC)(?P<year>\d{4})(?P<month>\d{2})(?P<seq>\d{3,5})(?P<suffix>[A-Z])?$",
            cleaned,
            re.IGNORECASE,
        )
    if not m:
        return None

    return {
        "type":   m.group("type").upper(),
        "year":   m.group("year"),
        "month":  m.group("month"),
        "seq":    m.group("seq").zfill(4),
        "suffix": (m.group("suffix") or "").upper(),
        "joined": f"{m.group('type').upper()}-{m.group('year')}-{m.group('month')}-{m.group('seq').zfill(4)}",
    }


class SummitDocketScraper(DocketScraper):

    county_id = "summit-oh"
    county_name = "Summit"

    async def scrape_case(self, case_number: str) -> DocketResult:
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
        )

        parsed = parse_summit_case_number(case_number)
        if not parsed:
            result.classification = "unknown"
            result.classification_reason = f"case number not parseable: {case_number}"
            return result

        diag_dir = Path("data/diagnostics/summit-oh")
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

            async def dump(label, selector="body", inline_chars=3000):
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
                # ─── Step 1: Disclaimer ─────────────────────────────────────
                print(f"      ▶ step 1: load disclaimer page")
                await page.goto(DISCLAIMER_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1500)
                await snap("01_disclaimer")
                await dump("01_disclaimer_body", "body", inline_chars=2000)

                clicked_agree = await self._click_agree(page)
                await page.wait_for_timeout(2000)
                await snap("02_after_agree")
                print(f"      → after Agree: url={page.url}")
                if not clicked_agree:
                    result.classification = "unknown"
                    result.classification_reason = "could not click disclaimer Agree link"
                    return result

                # ─── Step 2: Division select → Civil ────────────────────────
                print(f"      ▶ step 2: navigate to Civil division")
                await self._click_civil(page)
                await page.wait_for_timeout(2000)
                await snap("03_civil_landing")
                await dump("03_civil_landing", "body", inline_chars=3000)
                print(f"      → civil landing: url={page.url}")

                # ─── Step 3: Search by Case Number ──────────────────────────
                print(f"      ▶ step 3: navigate to Search by Case Number")
                await self._goto_case_number_search(page)
                await page.wait_for_timeout(2000)
                await snap("04_search_form")
                await dump("04_search_form", "form, body", inline_chars=4000)
                print(f"      → search form: url={page.url}")

                # ─── Step 4: Fill split fields + submit ─────────────────────
                print(f"      ▶ step 4: fill split fields {parsed}")
                await self._dump_search_inputs(page)
                filled = await self._fill_split_fields(page, parsed)
                if not filled:
                    result.classification = "unknown"
                    result.classification_reason = "could not fill split case-number fields (recon pending)"
                    await snap("04b_fill_failed")
                    return result

                submitted = await self._submit_search(page)
                if not submitted:
                    result.classification = "unknown"
                    result.classification_reason = "could not submit search form (recon pending)"
                    await snap("04c_submit_failed")
                    return result

                await page.wait_for_load_state("domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2500)
                await snap("05_after_search")
                await dump("05_after_search", "body", inline_chars=4000)
                print(f"      → after search: url={page.url}")

                # ─── Step 5: Click into the case detail ─────────────────────
                print(f"      ▶ step 5: open case detail")
                opened = await self._open_case_detail(page, parsed)
                await page.wait_for_timeout(2000)
                await snap("06_case_detail")
                await dump("06_case_detail", "body", inline_chars=4000)
                print(f"      → case detail opened? {opened}; url={page.url}")
                if not opened:
                    result.classification = "unknown"
                    result.classification_reason = "could not open case detail from results (recon pending)"
                    return result

                result.case_url = page.url

                # ─── Step 6: Scrape summary + parties + judgment doc ───────
                # All recon-pending; per anti-fabrication rule, leave fields
                # empty rather than guessing. The follow-up patch will fill
                # these in once the dumps above expose the actual DOM.
                print(f"      ▶ step 6: scrape (recon-pending)")
                await self._scrape_summary(page, result)
                await self._scrape_docket(page, result)
                await self._scrape_parties(page, result)

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

    # ─── Step 1: Accept disclaimer (hyperlink, not form) ─────────────────

    async def _click_agree(self, page: Page) -> bool:
        """Click the 'Agree' hyperlink on Disclaimer.asp."""
        for sel in [
            "a:has-text('Agree')",
            "a[href*='SelectDivision' i]",
            "a[href$='SelectDivision.asp']",
        ]:
            try:
                link = page.locator(sel).first
                if await link.count() > 0 and await link.is_visible():
                    await link.click(timeout=5000)
                    print(f"      → clicked Agree via {sel}")
                    return True
            except Exception:
                continue
        print(f"      ⚠ could not find Agree link")
        return False

    # ─── Step 2: Civil division ─────────────────────────────────────────

    async def _click_civil(self, page: Page) -> None:
        """Navigate from SelectDivision.asp → Civil division landing."""
        for sel in [
            "a:has-text('Civil')",
            "a[href*='SelectDivisionCivil' i]",
            "a[href$='SelectDivisionCivil.aspx']",
        ]:
            try:
                link = page.locator(sel).first
                if await link.count() > 0 and await link.is_visible():
                    await link.click(timeout=5000)
                    print(f"      → clicked Civil via {sel}")
                    return
            except Exception:
                continue
        # Fallback: direct nav (session cookie should already be set)
        print(f"      → falling back to direct nav to SelectDivisionCivil.aspx")
        await page.goto(
            f"{CLERK_HOST}/PublicSite/SelectDivisionCivil.aspx",
            wait_until="domcontentloaded",
            timeout=20000,
        )

    # ─── Step 3: Search by Case Number ──────────────────────────────────

    async def _goto_case_number_search(self, page: Page) -> None:
        """Navigate to /PublicSite/SearchByCaseNbrCivil.aspx."""
        for sel in [
            "a:has-text('Case Number')",
            "a:has-text('Search by Case')",
            "a[href*='SearchByCaseNbr' i]",
        ]:
            try:
                link = page.locator(sel).first
                if await link.count() > 0 and await link.is_visible():
                    await link.click(timeout=5000)
                    print(f"      → clicked case-number search via {sel}")
                    return
            except Exception:
                continue
        print(f"      → falling back to direct nav to SearchByCaseNbrCivil.aspx")
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=20000)

    # ─── Step 4: Fill and submit ────────────────────────────────────────

    async def _dump_search_inputs(self, page: Page) -> None:
        """List every input/select/button on the search form for recon."""
        try:
            inputs = await page.query_selector_all("input, select")
            print(f"      → search form has {len(inputs)} input/select elements")
            for el in inputs[:25]:
                try:
                    el_id = await el.get_attribute("id") or ""
                    el_name = await el.get_attribute("name") or ""
                    el_type = await el.get_attribute("type") or ""
                    el_ml = await el.get_attribute("maxlength") or ""
                    visible = await el.is_visible()
                    print(f"         · id='{el_id}' name='{el_name}' type='{el_type}' maxlength='{el_ml}' visible={visible}")
                except Exception:
                    pass
            btns = await page.query_selector_all("input[type='submit'], button")
            print(f"      → search form has {len(btns)} button(s)")
            for el in btns[:10]:
                try:
                    el_id = await el.get_attribute("id") or ""
                    el_name = await el.get_attribute("name") or ""
                    el_value = await el.get_attribute("value") or ""
                    el_onclick = await el.get_attribute("onclick") or ""
                    visible = await el.is_visible()
                    print(f"         · btn id='{el_id}' name='{el_name}' value='{el_value}' onclick='{el_onclick[:60]}' visible={visible}")
                except Exception:
                    pass
        except Exception as e:
            print(f"      ⚠ dump_search_inputs failed: {e}")

    async def _fill_split_fields(self, page: Page, parsed: dict) -> bool:
        """Fill the Type/Year/Month/Sequence/Suffix fields.

        Exact field IDs are not yet known — try common ASP.NET WebForms
        naming conventions. If none match, return False so the run reports
        "recon pending" rather than half-filling the wrong fields.
        """
        # Candidates by visible/maxlength heuristic — recon will replace these
        # with exact IDs in the next iteration.
        attempts = [
            # (field_value, list of candidate selectors)
            (parsed["type"], [
                "select[id*='Type' i]",
                "select[name*='Type' i]",
                "input[id*='Type' i][maxlength='2']",
                "input[name*='Type' i]",
            ]),
            (parsed["year"], [
                "input[id*='Year' i]",
                "input[name*='Year' i]",
                "input[maxlength='4']",
            ]),
            (parsed["month"], [
                "input[id*='Month' i]",
                "input[name*='Month' i]",
                "input[maxlength='2']",
            ]),
            (parsed["seq"], [
                "input[id*='Seq' i]",
                "input[name*='Seq' i]",
                "input[id*='Number' i]",
                "input[maxlength='5']",
                "input[maxlength='4']",
            ]),
        ]
        any_filled = False
        for value, selectors in attempts:
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() == 0 or not await el.is_visible():
                        continue
                    tag = await el.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        await el.select_option(value=value, timeout=3000)
                    else:
                        await el.fill(value, timeout=3000)
                    print(f"      → filled {sel} ← '{value}'")
                    any_filled = True
                    break
                except Exception:
                    continue
        if parsed["suffix"]:
            for sel in ["input[id*='Suffix' i]", "input[name*='Suffix' i]"]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.fill(parsed["suffix"], timeout=3000)
                        print(f"      → filled {sel} ← '{parsed['suffix']}'")
                        break
                except Exception:
                    continue
        return any_filled

    async def _submit_search(self, page: Page) -> bool:
        for sel in [
            "input[type='submit'][value*='Search' i]",
            "input[type='submit']",
            "button:has-text('Search')",
            "button[type='submit']",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=5000)
                    print(f"      → clicked submit via {sel}")
                    return True
            except Exception:
                continue
        # Last-ditch: press Enter on a focused input
        try:
            await page.keyboard.press("Enter")
            print(f"      → pressed Enter as fallback submit")
            return True
        except Exception:
            return False

    # ─── Step 5: Open case detail ───────────────────────────────────────

    async def _open_case_detail(self, page: Page, parsed: dict) -> bool:
        """Click into the case from the results page. Recon-pending."""
        # ASP.NET search results typically render as a GridView with row-level
        # __doPostBack handlers OR direct hyperlinks per row. Until recon
        # shows the actual DOM, try anchor text matching the case number.
        case_text = parsed["joined"]
        for sel in [
            f"a:has-text('{case_text}')",
            f"a:has-text('{parsed['type']}{parsed['year']}{parsed['month']}{parsed['seq']}')",
            "table[id*='Result' i] tr:nth-child(2) a",
            "table[id*='Result' i] tbody tr:first-child a",
        ]:
            try:
                link = page.locator(sel).first
                if await link.count() > 0 and await link.is_visible():
                    await link.click(timeout=5000)
                    print(f"      → clicked result via {sel}")
                    return True
            except Exception:
                continue
        return False

    # ─── Step 6: Stubs — refine after recon ─────────────────────────────

    async def _scrape_summary(self, page: Page, result: DocketResult) -> None:
        """Stub. Will be filled once 06_case_detail dump shows the layout."""
        text = await page.inner_text("body")
        result.kill_signals = self.detect_kill_signals(text)
        proof = self.detect_proof_of_surplus(text)
        if proof:
            result.proof_of_surplus = proof

    async def _scrape_docket(self, page: Page, result: DocketResult) -> None:
        """Stub. Apply the same strict JUDGMENT classifier as Montgomery
        once the docket-row DOM is known. Until then, prayer stays 0 per
        the anti-fabrication rule."""
        text = await page.inner_text("body")
        result.kill_signals = list(set(
            result.kill_signals + self.detect_kill_signals(text)
        ))
        result.competing_filers = self.detect_competing_filers(text)
        print(f"      → docket scrape: recon-pending, prayer stays $0")

    async def _scrape_parties(self, page: Page, result: DocketResult) -> None:
        """Stub. Recon-pending."""
        return
