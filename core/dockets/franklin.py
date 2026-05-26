"""
SurplusIQ — Franklin County Docket Scraper

Franklin County uses the "Case Information Online" (CIO) portal at:
  https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/

Key difference from Cuyahoga: Franklin does NOT show a structured
"Prayer Amount" field. The real debt must be extracted from the
Summary Judgment PDF filed in the docket.

Navigation flow (expected):

  1. https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/
     → Conditions of Use page — must check "I agree" and click accept

  2. Search page appears — paste full case number (e.g. "22CV5948")

  3. Case detail page shows: case title, filing date, status, parties,
     docket entries with links to PDFs

  4. Scan docket entries for "JUDGMENT" / "DECREE" — open the PDF,
     extract dollar amounts with pdfplumber

Case number format from auction scraper:
  Raw:     22CV5948 (16295)
  Parsed:  year=2022, type=CV, number=5948
  Stripped: "(16295)" is the auction batch ID
"""

from __future__ import annotations
import re
import asyncio
import tempfile
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult, DocketEvent


BASE_URL = "https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline"
LANDING_URL = f"{BASE_URL}/"


def parse_franklin_case_number(raw: str) -> Optional[dict]:
    """
    Parse a Franklin County case number into search components.

    Accepts variations:
      '22CV5948 (16295)'    -> {year: 2022, prefix: CV, number: 5948, search_text: '22CV5948'}
      '22CV5948'            -> same
      '24CV003247'          -> {year: 2024, prefix: CV, number: 3247, search_text: '24CV003247'}
      '2024CV003247'        -> {year: 2024, prefix: CV, number: 3247, search_text: '24CV003247'}

    Returns None if not parseable.
    """
    if not raw:
        return None

    # Strip auction suffix like " (16295)"
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())
    # Remove spaces/dashes but keep the core
    cleaned = re.sub(r"[-\s]", "", cleaned).upper()

    # Try 2-digit year: YYCV####
    m = re.match(r"^(\d{2})(CV)(\d+)$", cleaned)
    if m:
        yr = int(m.group(1))
        year = 2000 + yr if yr <= 30 else 1900 + yr
        return {
            "year": year,
            "prefix": m.group(2),
            "number": int(m.group(3)),
            "search_text": cleaned,  # e.g. "22CV5948"
        }

    # Try 4-digit year: YYYYCV####
    m = re.match(r"^(\d{4})(CV)(\d+)$", cleaned)
    if m:
        return {
            "year": int(m.group(1)),
            "prefix": m.group(2),
            "number": int(m.group(3)),
            "search_text": cleaned,
        }

    return None


def extract_debt_from_pdf_bytes(pdf_bytes: bytes) -> Optional[float]:
    """
    Extract the judgment/debt amount from a foreclosure judgment PDF.

    Strategy: scan the PDF text for dollar amounts near keywords like
    "judgment", "decree", "total", "amount due", "principal".
    Return the largest qualifying amount (likely the total judgment).
    """
    try:
        import pdfplumber
    except ImportError:
        print("      ⚠ pdfplumber not installed — cannot parse PDF")
        return None

    import io
    amounts = []
    full_text = ""

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:10]:  # Cap at 10 pages
                page_text = page.extract_text() or ""
                full_text += page_text + "\n"
    except Exception as e:
        print(f"      ⚠ PDF parse error: {e}")
        return None

    if not full_text.strip():
        return None

    text_lower = full_text.lower()

    # Look for dollar amounts near judgment-related keywords
    # Pattern: keyword within 300 chars of a dollar amount
    judgment_keywords = [
        "judgment", "decree", "amount due", "principal",
        "total amount", "sum of", "awarded", "ordered to pay",
        "indebtedness", "balance due", "amount owing",
    ]

    # Find all dollar amounts in the text
    dollar_pattern = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
    for match in dollar_pattern.finditer(full_text):
        try:
            amt = float(match.group(1).replace(",", ""))
        except ValueError:
            continue

        if amt < 1000:  # Filter trivial fees
            continue

        # Check if any judgment keyword appears within 500 chars before this amount
        start = max(0, match.start() - 500)
        context = text_lower[start:match.end()]
        if any(kw in context for kw in judgment_keywords):
            amounts.append(amt)

    if not amounts:
        # Fallback: just find the largest dollar amount > $10K in the doc
        for match in dollar_pattern.finditer(full_text):
            try:
                amt = float(match.group(1).replace(",", ""))
                if amt > 10000:
                    amounts.append(amt)
            except ValueError:
                continue

    return max(amounts) if amounts else None


class FranklinDocketScraper(DocketScraper):

    county_id = "franklin-oh"
    county_name = "Franklin"

    async def scrape_case(self, case_number: str) -> DocketResult:
        """Run the full scrape against one Franklin County case."""
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
        )

        parsed = parse_franklin_case_number(case_number)
        if not parsed:
            result.classification = "unknown"
            result.classification_reason = f"case number not parseable: {case_number}"
            return result

        # Diagnostic screenshots
        diag_dir = Path("data/diagnostics/franklin-oh")
        diag_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1400, "height": 900},
                ignore_https_errors=True,
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

            try:
                # ─── Step 1: Load portal and accept terms ───
                print(f"      ▶ step 1: load portal & accept terms")
                await page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                await snap("01_landing")

                terms_accepted = await self._accept_terms(page)
                await snap("02_after_terms")
                if not terms_accepted:
                    # Maybe terms were already accepted (session cookie)
                    print(f"      ⚠ terms acceptance uncertain, continuing...")

                # ─── Step 2: Search for the case ───
                print(f"      ▶ step 2: search for case {parsed['search_text']}")
                found = await self._search_case(page, parsed)
                await snap("03_after_search")
                print(f"      → landed at: {page.url}")
                print(f"      → search found case? {found}")

                if not found:
                    result.classification = "unknown"
                    result.classification_reason = (
                        f"search did not find case. URL: {page.url}"
                    )
                    return result

                result.case_url = page.url

                # ─── Step 3: Scrape case summary ───
                print(f"      ▶ step 3: scrape case summary")
                await self._scrape_summary(page, result)
                await snap("04_case_summary")
                print(f"      → title: {result.case_title[:60] if result.case_title else 'N/A'}")

                # ─── Step 4: Scrape docket entries + find judgment PDF ───
                print(f"      ▶ step 4: scrape docket entries")
                await self._scrape_docket(page, result)
                await snap("05_docket")
                print(f"      → events={len(result.events)}, kill={result.kill_signals}")
                print(f"      → prayer_amount=${result.prayer_amount:,.0f} (source: {result.debt_source or 'none'})")

                # ─── Step 5: Scrape parties ───
                print(f"      ▶ step 5: scrape parties")
                await self._scrape_parties(page, result)
                await snap("06_parties")
                print(f"      → defendants={len(result.defendants)}")

                # ─── Step 6: Classify ───
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

    # ─── Step 1: Accept Terms of Use ─────────────────────────────────────

    async def _accept_terms(self, page: Page) -> bool:
        """
        Franklin CIO shows a terms page on first visit.
        Look for a checkbox ("I agree") and an accept/submit button.
        """
        body_text = await page.inner_text("body")

        # If we're already past terms (e.g. search form visible), skip
        if "case number" in body_text.lower() and "search" in body_text.lower():
            print(f"      → terms already accepted (search form visible)")
            return True

        # Try to find and click the agreement checkbox
        checkbox_clicked = False
        for sel in [
            "input[type='checkbox']",
            "input[id*='agree' i]",
            "input[name*='agree' i]",
            "input[id*='terms' i]",
            "input[id*='accept' i]",
        ]:
            try:
                cb = page.locator(sel).first
                if await cb.count() > 0:
                    await cb.check(timeout=3000)
                    checkbox_clicked = True
                    print(f"      → checked terms checkbox: {sel}")
                    break
            except Exception:
                continue

        # Also try clicking text that looks like the agreement label
        if not checkbox_clicked:
            for text_sel in [
                "text=/I have read/i",
                "text=/I agree/i",
                "text=/accept/i",
            ]:
                try:
                    await page.click(text_sel, timeout=3000)
                    checkbox_clicked = True
                    print(f"      → clicked terms text: {text_sel}")
                    break
                except Exception:
                    continue

        await page.wait_for_timeout(500)

        # Click the submit/accept/continue button
        for sel in [
            "input[type='submit']",
            "button[type='submit']",
            "input[value*='Accept' i]",
            "input[value*='Submit' i]",
            "input[value*='Continue' i]",
            "button:has-text('Accept')",
            "button:has-text('Submit')",
            "button:has-text('Continue')",
            "a:has-text('Accept')",
            "a:has-text('Continue')",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click(timeout=5000)
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)
                    print(f"      → clicked accept button: {sel}")
                    return True
            except Exception:
                continue

        # Last resort: try pressing Enter
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)

        return checkbox_clicked

    # ─── Step 2: Search for a case ───────────────────────────────────────

    async def _search_case(self, page: Page, parsed: dict) -> bool:
        """
        Find and fill the case number search field, submit, and verify
        we land on a case detail page.
        """
        search_text = parsed["search_text"]

        # Wait for the search page to be ready
        await page.wait_for_timeout(1500)

        # Strategy 1: Look for a case number input field
        input_filled = False
        for sel in [
            "input[id*='CaseNumber' i]",
            "input[name*='CaseNumber' i]",
            "input[id*='caseNum' i]",
            "input[name*='caseNum' i]",
            "input[id*='case' i]",
            "input[name*='case' i]",
            "input[id*='search' i]",
            "input[name*='search' i]",
            "input[type='text']",
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    # Check if it's visible
                    if await el.is_visible():
                        await el.fill(search_text, timeout=3000)
                        input_filled = True
                        print(f"      → filled search input: {sel} with '{search_text}'")
                        break
            except Exception:
                continue

        if not input_filled:
            # Dump page text for diagnostics
            body = await page.inner_text("body")
            print(f"      ⚠ could not find search input. Page text (first 500): {body[:500]}")
            return False

        await page.wait_for_timeout(500)

        # Click search/submit button
        submitted = False
        for sel in [
            "input[type='submit']",
            "button[type='submit']",
            "input[value*='Search' i]",
            "input[value*='Submit' i]",
            "button:has-text('Search')",
            "button:has-text('Submit')",
            "a:has-text('Search')",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=5000)
                    submitted = True
                    print(f"      → clicked search button: {sel}")
                    break
            except Exception:
                continue

        if not submitted:
            # Try Enter key as fallback
            await page.keyboard.press("Enter")
            print(f"      → pressed Enter to submit search")

        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3000)

        # Check if we landed on a case detail page or a results list
        current_url = page.url.lower()
        body_text = await page.inner_text("body")

        # If results list, try clicking the first matching case link
        if "search" in current_url or "result" in current_url:
            print(f"      → on search results page, looking for case link...")
            # Try clicking a link containing our case number
            for link_sel in [
                f"a:has-text('{search_text}')",
                f"text={search_text}",
                "table tbody tr:first-child a",
                "table tr:nth-child(2) a",  # first data row (skip header)
                ".case-link",
                "a[href*='caseDetail']",
                "a[href*='CaseDetail']",
            ]:
                try:
                    link = page.locator(link_sel).first
                    if await link.count() > 0:
                        await link.click(timeout=5000)
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                        await page.wait_for_timeout(2000)
                        print(f"      → clicked case link: {link_sel}")
                        break
                except Exception:
                    continue

        # Verify we have case content (title, parties, docket, etc.)
        body_text = await page.inner_text("body")
        body_lower = body_text.lower()
        has_case_indicators = any(kw in body_lower for kw in [
            "docket", "parties", "filing date", "case title",
            "plaintiff", "defendant", "foreclosure", "case summary",
            "case detail", "case information",
        ])

        return has_case_indicators

    # ─── Step 3: Scrape case summary ─────────────────────────────────────

    async def _scrape_summary(self, page: Page, result: DocketResult) -> None:
        """Extract case title, filing date, status from case detail page."""
        text = await page.inner_text("body")

        # Case title — look for "vs" pattern (plaintiff vs defendant)
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

        # Case type / designation
        type_m = re.search(r"(?:Case\s+)?Type\s*:?\s*([A-Z][A-Za-z /\-]+)", text)
        if type_m:
            result.case_designation = type_m.group(1).strip()[:80]

        # Scan for kill signals and proof of surplus in summary text
        result.kill_signals = self.detect_kill_signals(text)
        proof = self.detect_proof_of_surplus(text)
        if proof:
            result.proof_of_surplus = proof

    # ─── Step 4: Scrape docket + extract judgment from PDF ───────────────

    async def _scrape_docket(self, page: Page, result: DocketResult) -> None:
        """
        Navigate to docket entries. Scan for kill signals.
        Find judgment/decree entries and attempt to download & parse their PDFs.
        """
        # Try clicking a "Docket" or "Case Activity" tab/link
        docket_clicked = False
        for sel in [
            "a:has-text('Docket')",
            "a:has-text('Case Activity')",
            "a:has-text('Events')",
            "a:has-text('Entries')",
            "a[href*='docket' i]",
            "a[href*='activity' i]",
            "a[href*='entries' i]",
        ]:
            try:
                link = page.locator(sel).first
                if await link.count() > 0:
                    await link.click(timeout=5000)
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)
                    docket_clicked = True
                    print(f"      → navigated to docket: {sel}")
                    break
            except Exception:
                continue

        # Get all text for signal scanning
        docket_text = await page.inner_text("body")

        # Kill signals + proof + competing filers from docket text
        result.kill_signals = list(set(
            result.kill_signals + self.detect_kill_signals(docket_text)
        ))
        result.competing_filers = self.detect_competing_filers(docket_text)
        proof = self.detect_proof_of_surplus(docket_text)
        if proof and not result.proof_of_surplus:
            result.proof_of_surplus = proof

        # Owner's claim check
        if re.search(r"owner'?s?\s+claim", docket_text, re.IGNORECASE):
            result.owner_filed_claim = True

        # Extract docket events from table rows
        events = []
        rows = await page.query_selector_all("table tr, tr")
        for row in rows:
            row_text = (await row.inner_text()).strip()
            if not row_text:
                continue
            # Look for date patterns: MM/DD/YYYY
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

        # Update last_activity_date
        if events:
            sorted_events = sorted(events, key=lambda e: e.filing_date, reverse=True)
            result.last_activity_date = sorted_events[0].filing_date

        # ─── Judgment PDF extraction ───
        # Look for links to judgment/decree PDFs in the docket
        await self._extract_judgment_from_pdf(page, result)

    async def _extract_judgment_from_pdf(self, page: Page, result: DocketResult) -> None:
        """
        Find judgment-related docket entries that have PDF links.
        Download the PDF and extract the debt amount.
        """
        # Already have prayer amount from another source? Skip.
        if result.prayer_amount > 0:
            return

        # Find all links on the page
        links = await page.query_selector_all("a")
        judgment_links = []

        for link in links:
            try:
                text = (await link.inner_text()).strip().lower()
                href = await link.get_attribute("href") or ""
            except Exception:
                continue

            # Look for judgment-related entries
            judgment_keywords = [
                "judgment", "decree", "summary judgment",
                "default judgment", "foreclosure judgment",
                "final judgment", "entry of judgment",
                "magistrate decision",
            ]
            is_judgment = any(kw in text for kw in judgment_keywords)

            # Also check if the link points to a document/PDF
            is_doc_link = any(ext in href.lower() for ext in [
                ".pdf", "document", "doc", "image", "view",
            ])

            if is_judgment or (is_doc_link and any(kw in text for kw in judgment_keywords)):
                judgment_links.append((link, text, href))

        if not judgment_links:
            print(f"      → no judgment PDF links found in docket")
            # Try to find dollar amounts directly in the docket text
            docket_text = await page.inner_text("body")
            self._extract_debt_from_text(docket_text, result)
            return

        # Try each judgment link — download the PDF and extract amount
        for link_el, link_text, href in judgment_links[:3]:  # Cap at 3 attempts
            print(f"      → trying judgment link: '{link_text[:60]}' → {href[:80]}")
            try:
                # Use Playwright's download mechanism
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
                # Download didn't trigger — might open in a new tab or inline
                pass

            # Fallback: try navigating to the link and reading page content
            try:
                # Open in same tab, read content, navigate back
                original_url = page.url
                full_href = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
                response = await page.goto(full_href, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)

                # Check if we got a PDF (content-type) or an HTML viewer
                content_type = response.headers.get("content-type", "") if response else ""

                if "pdf" in content_type:
                    # Direct PDF — get the bytes via the response
                    body = await response.body()
                    amount = extract_debt_from_pdf_bytes(body)
                    if amount:
                        result.prayer_amount = amount
                        result.debt_source = "pdf_extract"
                        print(f"      ✅ extracted debt from direct PDF: ${amount:,.2f}")
                        await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                        return
                else:
                    # HTML page — maybe an embedded viewer, scan text
                    viewer_text = await page.inner_text("body")
                    self._extract_debt_from_text(viewer_text, result)
                    if result.prayer_amount > 0:
                        print(f"      ✅ extracted debt from doc viewer: ${result.prayer_amount:,.2f}")
                        await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                        return

                # Navigate back for next attempt
                await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)
            except Exception as e:
                print(f"      ⚠ link follow failed: {e}")
                continue

        # Last resort: scan docket text for inline dollar amounts
        if result.prayer_amount == 0:
            docket_text = await page.inner_text("body")
            self._extract_debt_from_text(docket_text, result)

    def _extract_debt_from_text(self, text: str, result: DocketResult) -> None:
        """
        Extract debt amount from inline docket text (no PDF needed).
        Looks for dollar amounts near judgment keywords.
        """
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

    # ─── Step 5: Scrape parties ──────────────────────────────────────────

    async def _scrape_parties(self, page: Page, result: DocketResult) -> None:
        """Extract plaintiff and defendants from the case page."""
        # Try clicking a "Parties" tab/link
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

        # Extract plaintiff
        p_m = re.search(r"PLAINTIFF\s*:?\s*\n?\s*([^\n]+)", text, re.IGNORECASE)
        if p_m:
            result.plaintiff = p_m.group(1).strip()[:200]

        # Extract defendants
        defendants = []
        for m in re.finditer(r"DEFENDANT\s*:?\s*\n?\s*([^\n]+)", text, re.IGNORECASE):
            name = m.group(1).strip()[:200]
            if name and name not in defendants:
                defendants.append(name)
        result.defendants = defendants

        # Identify creditors among defendants
        creditor_keywords = [
            "LLC", "BANK", "TREASURER", "IRS", "STATE OF", "COUNTY",
            "CITY OF", "REVENUE", "DEPARTMENT", "ASSOCIATION", "TRUST",
            "FINANCIAL", "MORTGAGE", "CAPITAL", "FUND", "SERVICES", "INC",
        ]
        for name in defendants:
            name_upper = name.upper()
            if any(kw in name_upper for kw in creditor_keywords):
                result.additional_parties.append(name)
