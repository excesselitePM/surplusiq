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

                # ─── Step 5: Submit and wait for navigation to CaseDetail ───
                # Summit's case-number search posts back to itself and then
                # redirects to /PublicSite/CaseDetail.aspx on an exact match.
                # If the case number is invalid the form re-renders inline
                # ("Is Not A Valid Case Number") — same URL. So we wait for
                # the URL to change instead of for domcontentloaded.
                async with page.expect_navigation(
                    url=re.compile(r"/PublicSite/CaseDetail\.aspx", re.I),
                    timeout=20000,
                    wait_until="domcontentloaded",
                ) as nav:
                    submitted = await self._submit_search(page)
                    if not submitted:
                        result.classification = "unknown"
                        result.classification_reason = "could not submit search form"
                        await snap("04c_submit_failed")
                        return result
                try:
                    await nav.value
                except PWTimeout:
                    # Stayed on the form → server rejected the search.
                    await snap("05_search_rejected")
                    await dump("05_search_rejected", "body", inline_chars=4000)
                    result.classification = "unknown"
                    result.classification_reason = (
                        f"search did not navigate to CaseDetail (url={page.url})"
                    )
                    return result

                await page.wait_for_timeout(1500)
                await snap("05_case_detail")
                await dump("05_case_detail", "body", inline_chars=4000)
                print(f"      → landed on case detail: url={page.url}")
                result.case_url = page.url

                # ─── Step 6: Scrape summary + parties + judgment doc ───────
                # All tab content (Parties + Docket + Images) is in the
                # initial CaseDetail.aspx DOM — Infragistics WebTab just
                # shows/hides via CSS. No tab clicks required.
                print(f"      ▶ step 6: scrape case detail")
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
        """Fill the Type/Year/Month/Sequence/Suffix fields by exact ID.

        ASP.NET WebForms IDs captured from the recon dump:
          #ContentPlaceHolder1_tbCase1Prefix  (maxlength=2) ← Type   (CV/MI/AC)
          #ContentPlaceHolder1_tbCase1Part1   (maxlength=4) ← Year
          #ContentPlaceHolder1_tbCase1Part2   (maxlength=2) ← Month
          #ContentPlaceHolder1_tbCase1Part3   (maxlength=4) ← Sequence
          #ContentPlaceHolder1_tbCase1Suffix  (maxlength=3) ← Suffix (optional)

        Submit button: #ContentPlaceHolder1_btnOk.
        """
        plan = [
            ("#ContentPlaceHolder1_tbCase1Prefix", parsed["type"]),
            ("#ContentPlaceHolder1_tbCase1Part1",  parsed["year"]),
            ("#ContentPlaceHolder1_tbCase1Part2",  parsed["month"]),
            ("#ContentPlaceHolder1_tbCase1Part3",  parsed["seq"]),
        ]
        # IMPORTANT: do not fill the Suffix field from auction parcel-letters.
        # Summit's auction data tags multi-parcel foreclosures as ...A / ...B
        # / ...C — that letter is the parcel index, not the clerk-side case
        # suffix. The clerk's case number is CV-YYYY-MM-#### with no suffix,
        # and submitting the parcel letter triggers the server's
        # "Is Not A Valid Case Number" validator (recon-verified). If a real
        # suffix-bearing case format ever shows up, the parser will need to
        # distinguish parcel letters from clerk suffixes — for now treat
        # parsed["suffix"] as informational only.

        all_ok = True
        for sel, value in plan:
            try:
                el = page.locator(sel).first
                if await el.count() == 0:
                    print(f"      ⚠ missing {sel} — search form layout changed")
                    all_ok = False
                    continue
                await el.fill(value, timeout=3000)
                print(f"      → filled {sel} ← '{value}'")
            except Exception as e:
                print(f"      ⚠ fill {sel} failed: {type(e).__name__}: {e}")
                all_ok = False
        return all_ok

    async def _submit_search(self, page: Page) -> bool:
        sel = "#ContentPlaceHolder1_btnOk"
        try:
            btn = page.locator(sel).first
            if await btn.count() == 0:
                print(f"      ⚠ submit button {sel} not found")
                return False
            await btn.click(timeout=5000)
            print(f"      → clicked submit via {sel}")
            return True
        except Exception as e:
            print(f"      ⚠ submit click failed: {type(e).__name__}: {e}")
            return False

    # ─── Step 6: Scrape case-detail subsections ──────────────────────────

    async def _scrape_summary(self, page: Page, result: DocketResult) -> None:
        """Read case header labels: caption, file date, case type, judge."""
        for label_id, attr in [
            ("#ContentPlaceHolder1_lblCaseCaption", "case_title"),
            ("#ContentPlaceHolder1_lblCaseType",   "case_designation"),
        ]:
            try:
                el = page.locator(label_id).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip()
                    if text:
                        setattr(result, attr, text[:200])
            except Exception:
                pass

        try:
            el = page.locator("#ContentPlaceHolder1_lblFileDate").first
            if await el.count() > 0:
                fd = (await el.inner_text()).strip()
                m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", fd)
                if m:
                    mm, dd, yyyy = m.groups()
                    result.filing_date = f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
        except Exception:
            pass

        # Kill / proof / competing-filer signals from the whole case body —
        # docket text is embedded in the DOM so a single body scan covers it.
        text = await page.inner_text("body")
        result.kill_signals = self.detect_kill_signals(text)
        proof = self.detect_proof_of_surplus(text)
        if proof:
            result.proof_of_surplus = proof
        result.competing_filers = self.detect_competing_filers(text)
        if re.search(r"owner'?s?\s+claim", text, re.IGNORECASE):
            result.owner_filed_claim = True

        print(f"      → case_title='{result.case_title[:80]}' filing_date='{result.filing_date}'")

    async def _scrape_docket(self, page: Page, result: DocketResult) -> None:
        """Walk gvDocketDetails rows, classify, fetch the judgment PDF.

        Each docket row inside #ContentPlaceHolder1_igtabCaseDetails__ctl1
        renders as a Bootstrap row with:
          - lblFilingDate_N  (date text)
          - lblByAttorney_N  (filer)
          - lblDocket_N      (description text)
          - HyperLink1_N     (anchor, href like 'DisplayImage.asp?gstrPDFOH=...')
        Some rows have no HyperLink1 (paperwork-only / no scan). Those are
        skipped from PDF candidates by definition.

        Strict JUDGMENT classifier — same rules as Montgomery: row text
        must contain an explicit judgment-document phrase AND must not
        contain any fee/cost/clerk/release/satisfaction marker. Supporting
        filings (motion/affidavit/notice) are logged but never fetched.
        """
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
        exclusion_markers = [
            "fee", "cost statement", "clerk fee", "deposit", "refund",
            "release of judgment", "release of lien", "release of liens",
            "partial release", "satisfaction of judgment", "satisfaction",
            "transcript", "subpoena", "praecipe", "writ of execution",
            "garnishment",
        ]
        supporting_doc_markers = [
            "motion:", "motion for", "affidavit", "notice:", "notice of filing",
            "memorandum", "brief in", "reply in support", "exhibit",
        ]

        # Pull every docket row label + matching hyperlink (if any).
        # Label IDs follow the pattern lblDocket_N; we find all N and read
        # the same row's HyperLink1_N when it exists.
        label_els = await page.query_selector_all(
            "[id^='ContentPlaceHolder1_igtabCaseDetails__ctl1_gvDocketDetails_lblDocket_']"
        )
        print(f"      → docket rows found: {len(label_els)}")

        rows = []  # (idx, text, filing_date_iso, href_or_none)
        for el in label_els:
            try:
                el_id = await el.get_attribute("id") or ""
                m = re.match(
                    r"ContentPlaceHolder1_igtabCaseDetails__ctl1_gvDocketDetails_lblDocket_(\d+)$",
                    el_id,
                )
                if not m:
                    continue
                idx = m.group(1)
                text = (await el.inner_text()).strip()
                if not text:
                    continue

                # Filing date
                fd_iso = ""
                try:
                    fd_el = page.locator(
                        f"#ContentPlaceHolder1_igtabCaseDetails__ctl1_gvDocketDetails_lblFilingDate_{idx}"
                    ).first
                    if await fd_el.count() > 0:
                        fd = (await fd_el.inner_text()).strip()
                        dm = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", fd)
                        if dm:
                            mm, dd, yyyy = dm.groups()
                            fd_iso = f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
                except Exception:
                    pass

                # PDF hyperlink (may be absent)
                href = ""
                try:
                    a_el = page.locator(
                        f"#ContentPlaceHolder1_igtabCaseDetails__ctl1_gvDocketDetails_HyperLink1_{idx}"
                    ).first
                    if await a_el.count() > 0:
                        href = (await a_el.get_attribute("href")) or ""
                except Exception:
                    pass

                rows.append((idx, text, fd_iso, href))
            except Exception:
                continue

        # Capture all events for audit (cap at 50, same as Montgomery)
        result.events = [
            DocketEvent(
                filing_date=fd or "",
                description=t[:200],
                pdf_url=h or "",
            ).__dict__
            for (_, t, fd, h) in rows[:50]
        ]
        if rows:
            sorted_events = sorted(
                [(fd, t) for (_, t, fd, _) in rows if fd],
                reverse=True,
            )
            if sorted_events:
                result.last_activity_date = sorted_events[0][0]

        # Classify each row
        judgment_candidates = []  # (prio, idx, text, href)
        supporting_seen = []
        excluded_count = 0
        no_pdf_count = 0

        for idx, text, _fd, href in rows:
            tl = text.lower()
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
            preview = text[:160].replace("\n", " ")
            if any(sup in tl for sup in supporting_doc_markers):
                supporting_seen.append((matched_prio, idx, preview))
                continue
            if not href:
                no_pdf_count += 1
                continue
            judgment_candidates.append((matched_prio, idx, preview, href))

        judgment_candidates.sort(key=lambda c: c[0])
        supporting_seen.sort(key=lambda c: c[0])

        print(
            f"      → judgment candidates: {len(judgment_candidates)} "
            f"(supporting only: {len(supporting_seen)}, "
            f"excluded fee/clerk/release: {excluded_count}, "
            f"no-PDF: {no_pdf_count})"
        )
        for prio, idx, preview, _ in judgment_candidates[:6]:
            print(f"         · [JUDGMENT] prio={prio} row={idx} :: {preview}")
        for prio, idx, preview in supporting_seen[:4]:
            print(f"         · [SUPPORTING — not fetched] prio={prio} row={idx} :: {preview}")

        if not judgment_candidates:
            print(f"      → no qualified judgment document; prayer stays $0 (lead → unknown)")
            return

        req = page.context.request
        for prio, idx, preview, href in judgment_candidates[:6]:
            full_url = href if href.startswith("http") else f"{CLERK_HOST}/PublicSite/{href}"
            try:
                resp = await req.get(full_url, timeout=20000)
                if not resp.ok:
                    print(f"      ⚠ PDF fetch row={idx}: HTTP {resp.status}")
                    continue
                pdf_bytes = await resp.body()
                if not pdf_bytes or len(pdf_bytes) < 1000:
                    print(f"      ⚠ PDF row={idx}: body too small ({len(pdf_bytes)} bytes)")
                    continue
                if pdf_bytes[:4] != b"%PDF":
                    # Summit's DisplayImage.asp may return an HTML viewer
                    # wrapping an embed/iframe pointing at the real PDF.
                    head = pdf_bytes[:200].decode("utf-8", errors="ignore")
                    print(f"      ⚠ PDF row={idx}: not a raw PDF (head: {head[:120]!r})")
                    continue
                amount, snippet = extract_debt_from_pdf_bytes(pdf_bytes)
                if amount:
                    result.prayer_amount = amount
                    result.debt_source = f"pdf_extract:docket_row_{idx}:judgment"
                    print(f"      ✅ extracted debt from PDF row={idx} [JUDGMENT]: ${amount:,.2f}")
                    print(f"      📄 PDF context (~400 chars around match):")
                    print(f"         {snippet}")
                    return
                else:
                    print(f"      → PDF row={idx} parsed but no debt keyword match")
            except Exception as e:
                print(f"      ⚠ PDF fetch row={idx} failed: {type(e).__name__}: {e}")

        print(f"      → all qualified judgment PDFs failed; prayer stays $0 (lead → unknown)")
        return

    async def _scrape_parties(self, page: Page, result: DocketResult) -> None:
        """Read plaintiff(s) and defendant(s) from the gv* GridViews.

        IDs follow:
          gvPlaintiff_lblPartyName_N   (N=0..)
          gvDefendant_lblPartyName_N   (N=0..)
          gvOtherParties_lblPartyName_N
        """
        plaintiffs = []
        defendants = []
        others = []

        for grid, target in [
            ("gvPlaintiff", plaintiffs),
            ("gvDefendant", defendants),
            ("gvOtherParties", others),
        ]:
            els = await page.query_selector_all(
                f"[id^='ContentPlaceHolder1_igtabCaseDetails__ctl0_{grid}_lblPartyName_']"
            )
            for el in els:
                try:
                    name = (await el.inner_text()).strip()[:200]
                    if name and name not in target:
                        target.append(name)
                except Exception:
                    continue

        if plaintiffs and not result.plaintiff:
            result.plaintiff = plaintiffs[0]
        result.defendants = defendants

        creditor_keywords = [
            "LLC", "BANK", "TREASURER", "IRS", "STATE OF", "COUNTY",
            "CITY OF", "REVENUE", "DEPARTMENT", "ASSOCIATION", "TRUST",
            "FINANCIAL", "MORTGAGE", "CAPITAL", "FUND", "SERVICES", "INC",
        ]
        for name in defendants + others:
            name_upper = name.upper()
            if any(kw in name_upper for kw in creditor_keywords):
                if name not in result.additional_parties:
                    result.additional_parties.append(name)
        print(f"      → parties: {len(plaintiffs)} plaintiff(s), {len(defendants)} defendant(s), {len(others)} other")
