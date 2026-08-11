"""Franklin + Hamilton Cloudflare re-test — INVESTIGATION ONLY.

Plain chromium, DEFAULT launch args (no --disable-web-security / --no-sandbox),
realistic UA, human-like referer chain (county homepage first, then case
search), then a 20s patience window to see whether any JS challenge resolves
on its own. Captures status, cf-* headers, title, challenge markers, and
screenshots at each stage.

Usage: python cf_probe.py <outdir>
"""
import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "cf_probe_out")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TARGETS = {
    "franklin": [
        ("home",   "https://clerk.franklincountyohio.gov/"),
        ("search", "https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/"),
    ],
    "hamilton": [
        ("home",   "https://courtclerk.org/"),
        ("search", "https://courtclerk.org/records-search/case-number-search/"),
    ],
}

CHALLENGE_MARKERS = ("just a moment", "checking your browser", "challenge-form",
                     "cf-chl", "verify you are human", "attention required",
                     "cloudflare", "enable javascript and cookies")


async def snapshot(page, resp, county, label):
    info = {"county": county, "stage": label, "url": page.url}
    if resp:
        info["status"] = resp.status
        h = await resp.all_headers()
        info["headers"] = {k: v for k, v in h.items()
                           if k.lower() in ("cf-ray", "cf-mitigated", "cf-cache-status",
                                            "server", "cf-chl-bypass", "retry-after")}
    try:
        info["title"] = await page.title()
        body = (await page.inner_text("body"))[:600].lower()
        info["challenge_markers_hit"] = [m for m in CHALLENGE_MARKERS if m in body]
        info["body_head"] = body[:300]
    except Exception as e:
        info["dom_error"] = f"{type(e).__name__}: {e}"
    await page.screenshot(path=str(OUT / f"{county}-{label}.png"), full_page=False)
    print(json.dumps(info, indent=1))
    return info


async def probe(county, steps):
    print(f"\n{'='*25} {county.upper()} {'='*25}")
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)  # default args, nothing stripped
        ctx = await browser.new_context(user_agent=UA,
                                        viewport={"width": 1366, "height": 850},
                                        locale="en-US")
        page = await ctx.new_page()
        for label, url in steps:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as e:
                print(f"  goto {label} issue: {type(e).__name__}: {e}")
                resp = None
            results.append(await snapshot(page, resp, county, label))
            # Human-like dwell between pages
            await page.wait_for_timeout(2500)

        # Patience window on the final (search) page: does a JS challenge
        # self-resolve? Re-inspect after 20s.
        await page.wait_for_timeout(20000)
        results.append(await snapshot(page, None, county, "search-after-20s"))
        await browser.close()
    return results


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    all_results = {}
    for county, steps in TARGETS.items():
        try:
            all_results[county] = await probe(county, steps)
        except Exception as e:
            print(f"  ❌ {county}: {type(e).__name__}: {e}")
    (OUT / "results.json").write_text(json.dumps(all_results, indent=1))
    print(f"\nsaved → {OUT}/results.json")

asyncio.run(main())
