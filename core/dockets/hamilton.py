"""
SurplusIQ — Hamilton County Docket Probe (recon-only)

Hamilton's clerk portal at https://courtclerk.org/ was previously flagged
as returning 403 to automated traffic. This module is intentionally a
probe — it does NOT attempt to bypass any block. It loads the portal
pages with both an HTTP request and a real browser context, captures
the status code / headers / first body bytes, and saves the response
HTML for inspection.

Purpose: establish from a CURRENT GitHub Actions run whether the portal
is still hard-blocking automation, what the block looks like (403, CF
challenge page, custom WAF block page, CAPTCHA, etc.), and which entry
points (root / records-search / case-search) are gated.

No fabrication, no fallback math. Every probed case returns
classification=unknown with a reason that quotes the actual block
response. This is recon, not extraction.
"""

from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from .base import DocketScraper, DocketResult


PORTAL_ROOT = "https://courtclerk.org/"
RECORDS_SEARCH = "https://courtclerk.org/records-search/case-number-search/"


class HamiltonDocketScraper(DocketScraper):

    county_id = "hamilton-oh"
    county_name = "Hamilton"

    async def scrape_case(self, case_number: str) -> DocketResult:
        result = DocketResult(
            county_id=self.county_id,
            case_number=case_number,
            scraped_at=datetime.now().isoformat(),
        )
        diag = Path("data/diagnostics/hamilton-oh")
        diag.mkdir(parents=True, exist_ok=True)

        findings: list[str] = []

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

            # ─── Probe 1: bare HTTP request (no browser JS) ─────────────
            for label, url in [("root", PORTAL_ROOT), ("records_search", RECORDS_SEARCH)]:
                try:
                    resp = await context.request.get(url, timeout=20000)
                    status = resp.status
                    server = resp.headers.get("server", "")
                    cf_ray = resp.headers.get("cf-ray", "")
                    ct = resp.headers.get("content-type", "")
                    body = await resp.body()
                    head = body[:600].decode("utf-8", errors="ignore")
                    findings.append(
                        f"HTTP {label}: status={status} server='{server}' "
                        f"cf-ray='{cf_ray}' content-type='{ct}' bytes={len(body)}"
                    )
                    print(f"      🔎 HTTP {label}: {findings[-1]}")
                    print(f"         body head: {head[:300]!r}")
                    ts = datetime.now().strftime("%H%M%S")
                    (diag / f"{ts}-http_{label}_{status}.html").write_bytes(body)
                except Exception as e:
                    findings.append(f"HTTP {label}: error {type(e).__name__}: {e}")
                    print(f"      ⚠ HTTP {label} failed: {e}")

            # ─── Probe 2: real browser page.goto on each URL ────────────
            page = await context.new_page()
            for label, url in [("root", PORTAL_ROOT), ("records_search", RECORDS_SEARCH)]:
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    status = resp.status if resp else 0
                    landed = page.url
                    body_text = (await page.inner_text("body"))[:1200]
                    is_cf_challenge = (
                        "Just a moment..." in body_text
                        or "Checking your browser" in body_text
                        or "Cloudflare" in body_text
                    )
                    is_403 = status == 403 or "403 Forbidden" in body_text
                    findings.append(
                        f"BROWSER {label}: status={status} landed={landed} "
                        f"cf_challenge={is_cf_challenge} forbidden={is_403}"
                    )
                    print(f"      🔎 BROWSER {label}: {findings[-1]}")
                    print(f"         body text head: {body_text[:300]!r}")
                    ts = datetime.now().strftime("%H%M%S")
                    await page.screenshot(
                        path=str(diag / f"{ts}-browser_{label}.png"),
                        full_page=True,
                    )
                    html = await page.content()
                    (diag / f"{ts}-browser_{label}.html").write_text(html, encoding="utf-8")
                except PWTimeout as e:
                    findings.append(f"BROWSER {label}: timeout {e}")
                    print(f"      ⚠ BROWSER {label} timeout: {e}")
                except Exception as e:
                    findings.append(f"BROWSER {label}: error {type(e).__name__}: {e}")
                    print(f"      ⚠ BROWSER {label} failed: {e}")

            await browser.close()

        result.classification = "unknown"
        result.classification_reason = "probe-only: " + " | ".join(findings)[:400]
        return result
