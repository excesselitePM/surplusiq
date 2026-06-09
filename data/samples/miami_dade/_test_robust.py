"""Robustness probe for the Miami-Dade docket detector.

Answers three questions the parser's correctness depends on:
  1. Does aria-label="View details for ..." capture EVERY docket entry, or a
     subset? (compares parser-visible titles vs the Dockets "N results
     returned" count, and vs all fw-bold titles in the docket section)
  2. Is the docket lazy-loaded / paginated? (counts rendered docket cards,
     scrolls to bottom, re-counts; expands the Dockets accordion first)
  3. Are there docket rows WITHOUT the aria-label that carry claim/kill text?

Also dumps the full docket-entry list for each case so kills/clean calls can
be eyeballed against the real docket.

Run: PYTHONPATH=. python data/samples/miami_dade/_test_robust.py
"""
import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright
from core.dockets.miami_dade import (
    parse_miami_dade_case_number, LANDING_URL, REAL_UA,
)

CASES = [
    "2025-010668-CA-01",   # clean -> pursuable
    "2024-020538-CA-01",   # killed (claim filed)
    "2025-000672-CA-01",   # killed (bankruptcy)
    "2019-001371-CA-01",   # killed (bankruptcy)
    "2017-021344-CA-01",   # killed (bankruptcy) - baseline
]

OUT = Path("data/samples/miami_dade")


async def analyze(page, case):
    # Expand every collapsible section so collapsed docket content is laid out.
    # (Dockets accordion renders with height:0 until clicked.)
    headers = page.locator("div.cursor-pointer:has(p)")
    n = await headers.count()
    for i in range(n):
        try:
            await headers.nth(i).click(timeout=1500)
        except Exception:
            pass
    await page.wait_for_timeout(1500)

    # Scroll to bottom in steps to trigger any lazy rendering.
    for _ in range(12):
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await page.wait_for_timeout(400)

    # "Dockets N results returned"
    body = await page.inner_text("body")
    mdock = re.search(r"Dockets\s+(\d+)\s+results returned", body)
    claimed = int(mdock.group(1)) if mdock else -1

    # Parser-visible titles (what miami_dade._docket_titles keys on)
    aria = await page.eval_on_selector_all(
        "[aria-label^='View details for']",
        "els => els.map(e => e.getAttribute('aria-label').replace('View details for ',''))",
    )
    # All fw-bold titles (superset incl. hearings/parties chrome)
    fwbold = await page.eval_on_selector_all(
        "p.fs-5.fw-bold", "els => els.map(e => e.innerText.trim())"
    )

    # Isolate the Dockets accordion DOM and pull its row titles directly,
    # independent of aria-label, to catch rows the parser might miss.
    dock_titles = await page.evaluate(
        """() => {
            // find the 'Dockets' section header, then its sibling content
            const hs = Array.from(document.querySelectorAll('p'))
                .filter(p => /^Dockets$/.test(p.innerText.trim()));
            if (!hs.length) return {found:false, titles:[]};
            // climb to the section container
            let sec = hs[0];
            for (let i=0;i<6 && sec;i++) sec = sec.parentElement;
            if (!sec) return {found:false, titles:[]};
            // collect candidate row titles within this section
            const cards = sec.querySelectorAll('.TitleSearchTab');
            const titles = Array.from(cards).map(c => {
                const p = c.querySelector('p');
                const al = c.getAttribute('aria-label') || '';
                return {p: p ? p.innerText.trim() : '', aria: al};
            });
            return {found:true, count: cards.length, titles};
        }"""
    )

    return claimed, aria, fwbold, dock_titles


async def run_case(pw, case):
    parsed = parse_miami_dade_case_number(case)
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(viewport={"width":1400,"height":900},
                                    ignore_https_errors=True, user_agent=REAL_UA)
    page = await ctx.new_page()
    try:
        await page.goto(LANDING_URL, wait_until="load", timeout=45000)
        await page.wait_for_selector("span[role='button']:has-text('Local Case')", timeout=20000)
        await page.locator("span[role='button']:has-text('Local Case')").first.click()
        await page.wait_for_selector("#caseYear", timeout=20000)
        await page.locator("#caseYear").first.select_option(value=str(parsed["year"]))
        seq = page.locator("#caseSeq").first
        await seq.click(); await seq.fill(""); await seq.type(parsed["number"], delay=40)
        await page.locator("#caseCode").first.select_option(value=parsed["case_code"])
        await page.wait_for_timeout(500)
        loc = page.locator("#caseLocation").first
        opts = await loc.evaluate("el=>Array.from(el.options).map(o=>o.value).filter(v=>v)")
        if parsed["location"] in opts:
            await loc.select_option(value=parsed["location"])
        elif opts:
            await loc.select_option(value=opts[0])
        await page.locator("button[type='submit']:has-text('SEARCH')").first.click()
        await page.wait_for_url("**/searchResults*", timeout=25000)
        await page.wait_for_load_state("networkidle", timeout=15000)
        claimed, aria, fwbold, dock = await analyze(page, case)
        (OUT / f"robust_{case}.html").write_text(await page.content(), encoding="utf-8")
        return claimed, aria, fwbold, dock
    finally:
        await browser.close()


KILL_RE = re.compile(r"(surplus|claim|disburs|vacate|set aside|cancel|bankrupt|stay|intervene|dismiss)", re.I)


async def main():
    async with async_playwright() as pw:
        for case in CASES:
            print(f"\n{'='*70}\n{case}\n{'='*70}")
            try:
                claimed, aria, fwbold, dock = await run_case(pw, case)
            except Exception as e:
                print(f"  FETCH FAILED: {type(e).__name__}: {e}")
                continue
            dock_cards = dock.get("count", 0) if dock.get("found") else 0
            dock_with_aria = sum(1 for t in dock.get("titles", []) if t["aria"])
            dock_without_aria = [t for t in dock.get("titles", []) if not t["aria"]]
            print(f"  Dockets claimed (N results returned): {claimed}")
            print(f"  Docket cards rendered in section     : {dock_cards}")
            print(f"  ...of which carry aria-label         : {dock_with_aria}")
            print(f"  parser-visible aria titles (whole pg): {len(aria)} ({len(set(a.lower() for a in aria))} unique)")
            print(f"  fw-bold titles (whole page)          : {len(fwbold)}")
            if dock_without_aria:
                print(f"  !! docket rows WITHOUT aria-label: {len(dock_without_aria)}")
                for t in dock_without_aria[:10]:
                    print(f"       · {t['p']!r}")
            # entries the parser would flag relevant
            flagged = [t["p"] for t in dock.get("titles", []) if KILL_RE.search(t["p"])]
            print(f"  RELEVANT docket entries (claim/kill/etc):")
            for t in flagged:
                print(f"       >> {t}")
            if not flagged:
                print("       (none)")
            # Full dump to file
            with open(OUT / f"robust_{case}.titles.txt", "w", encoding="utf-8") as f:
                f.write(f"Dockets claimed={claimed} rendered={dock_cards}\n\n")
                for i, t in enumerate(dock.get("titles", [])):
                    f.write(f"{i:3} aria={'Y' if t['aria'] else 'N'}  {t['p']}\n")
            print(f"  full titles -> robust_{case}.titles.txt")


if __name__ == "__main__":
    asyncio.run(main())
