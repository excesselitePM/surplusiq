"""Franklin CIO search-flow probe — INVESTIGATION ONLY, gentle single-case.

Accept disclaimer → case-number search (24 CV 009172) → confirm the case-detail
docket renders. Proves the SEARCH flow works (not just the landing page), from
whatever IP it runs on. No retries, human-paced waits.

Usage: python scripts/franklin_search_probe.py <outdir>
"""
import asyncio, re, sys
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "franklin_probe_out")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/"


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        ctx = await b.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
        page = await ctx.new_page()
        r = await page.goto(BASE, wait_until="networkidle", timeout=60000)
        print(f"landing: HTTP {r.status if r else '?'}  title={await page.title()!r}")
        await page.wait_for_timeout(2500)  # human pace
        async with page.expect_navigation(wait_until="networkidle", timeout=30000):
            await page.click("input[value='ACCEPT'], input[name='Accept']", timeout=12000)
        print(f"after disclaimer: title={await page.title()!r}  search form present="
              f"{await page.locator('#caseSeq_nh').count() > 0}")
        await page.wait_for_timeout(2500)
        await page.fill("#caseYear_nh", "24")
        await page.select_option("#caseType_nh", "CV")
        await page.fill("#caseSeq_nh", "009172")
        async with page.expect_navigation(wait_until="networkidle", timeout=30000):
            await page.click("#btnSearch")
        await page.wait_for_timeout(1500)
        body = re.sub(r"\s+", " ", await page.inner_text("body"))
        matched = "NO CASE MATCHED" not in body.upper()
        print(f"case-detail: title={await page.title()!r}  MATCHED={matched}")
        for kw in ["24 CV 009172", "FORECLOSURE", "KEYBANK", "STEPHEN L MCINTOSH", "PLAINTIFF"]:
            print(f"   marker {kw!r}: {kw in body.upper()}")
        await page.screenshot(path=str(OUT / "franklin-casedetail.png"), full_page=True)
        (OUT / "franklin-casedetail.txt").write_text(body[:8000], encoding="utf-8")
        await b.close()

asyncio.run(main())
