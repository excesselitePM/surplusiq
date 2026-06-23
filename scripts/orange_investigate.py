"""
Orange MyEClerk (Tyler Odyssey Portal) — INVESTIGATION PROBE (headless).

NOT production. Throwaway. Phase-1 questions: portal type (ASP.NET MVC, NOT
Duval's CoreCms.aspx), CAPTCHA?, anonymous public search path?, case→docket
navigation, full-docket capture — all from the Actions datacenter IP. Plus a
party-name search mode (the Duval method) for Phase-2 claim vocab.

mode=probe  : characterize landing + /Cases/Search, then open each case docket.
mode=party  : criteria search by party name (recovery firms) to find claim cases.

Saves HTML + screenshots + summary to data/samples/orange/ci/.
Usage: python scripts/orange_investigate.py <mode> "<cases-or-terms>"
"""
import sys, re, json, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "https://myeclerk.myorangeclerk.com"
LANDING = f"{BASE}/"
SEARCH = f"{BASE}/Cases/Search"
REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = Path("data/samples/orange/ci")
NET = re.compile(r"recaptcha|hcaptcha|captcha|/api/|/json|Search|Case|Docket", re.I)


def tag(cn): return re.sub(r"[^A-Za-z0-9]", "", cn).upper()


def captcha_markers(html, url):
    low = html.lower()
    m = (re.search(r"render=([0-9A-Za-z_\-]{30,})", html)
         or re.search(r'data-sitekey=["\']([^"\']+)', html))
    return {"any_recaptcha": "recaptcha" in low, "any_hcaptcha": "hcaptcha" in low,
            "v3_render": "api.js?render=" in low, "sitekey": m.group(1) if m else "",
            "challenge_text": any(t in low for t in
                ["i'm not a robot", "verify you are human", "unusual traffic", "press and hold"])}


async def characterize(page):
    return await page.evaluate(r"""() => {
        const vs=document.querySelector('#__VIEWSTATE');
        return {
          title: document.title, url: location.href,
          framework: {has_viewstate: !!vs, aspnet_form: !!document.querySelector('form[action*=".aspx"],form[method]'),
            react_root: !!document.querySelector('#root,[data-reactroot]'),
            mvc_hint: /\/Cases\/|\/Home\/|\/Account\//.test(document.body.innerHTML),
            scripts: Array.from(document.querySelectorAll('script[src]')).map(s=>s.src)
              .filter(s=>/recaptcha|tyler|odyssey|bundle|jquery|app|main/i.test(s)).slice(0,15)},
          inputs: Array.from(document.querySelectorAll('input,textarea,select')).map(i=>({
            id:i.id,name:i.name,type:i.type,placeholder:i.placeholder,aria:i.getAttribute('aria-label')||''})).slice(0,40),
          buttons: Array.from(document.querySelectorAll('button,input[type=submit],a[href*="Search"],a[href*="Case"],[role=button]'))
            .map(b=>({tag:b.tagName,id:b.id,text:(b.innerText||b.value||'').replace(/\s+/g,' ').trim().slice(0,40),
              href:(b.getAttribute('href')||'').slice(0,80)})).slice(0,40),
          body_head:(document.body.innerText||'').replace(/\s+/g,' ').trim().slice(0,500)
        };}""")


async def dump(page, label):
    OUT.mkdir(parents=True, exist_ok=True)
    try: (OUT/f"{label}.html").write_text(await page.content(), encoding="utf-8")
    except Exception: pass
    try: await page.screenshot(path=str(OUT/f"{label}.png"), full_page=True)
    except Exception: pass


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    arg = sys.argv[2].strip() if len(sys.argv) > 2 else ""
    items = [x.strip() for x in arg.split(",") if x.strip()]
    if not items:
        items = ["2024-CC-022302-O", "2023-CC-013712-O"] if mode == "probe" else ["SURPLUS", "RECOVERY"]
    OUT.mkdir(parents=True, exist_ok=True)
    netlog = []
    summary = {"mode": mode, "items": items, "results": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 1200},
                                        ignore_https_errors=True, user_agent=REAL_UA)
        ctx.on("request", lambda r: netlog.append(f"{r.method} {r.url[:150]}") if NET.search(r.url) else None)
        page = await ctx.new_page()

        # STAGE A: landing
        print("===== STAGE A: LANDING =====")
        try:
            await page.goto(LANDING, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  nav err: {e}")
        land_html = await dump_and_char(page, "A_landing", summary, "landing")
        # STAGE B: case search page
        print("\n===== STAGE B: /Cases/Search =====")
        try:
            await page.goto(SEARCH, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  nav err: {e}")
        await dump_and_char(page, "B_search", summary, "search")

        # STAGE C
        for it in items:
            print(f"\n===== STAGE C [{mode}]: {it} =====")
            rec = {"item": it, "steps": {}}
            try:
                if mode == "probe":
                    await probe_case(page, it, rec)
                else:
                    await party_search(page, it, rec)
            except Exception as e:
                rec["result"] = f"EXC: {type(e).__name__}: {str(e)[:140]}"
                await dump(page, f"ERR_{tag(it)}")
            summary["results"].append(rec)
            print(json.dumps(rec, indent=2)[:2500])

        await browser.close()

    summary["netlog"] = netlog[:50]
    (OUT/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n===== SUMMARY =====")
    print(json.dumps({k: summary.get(k) for k in ("mode",)}, indent=2))
    for r in summary["results"]:
        print(f"  {r['item']:<26} -> {r.get('result','')}")
    print(f"  netlog ({len(netlog)}):")
    for n in netlog[:20]:
        print(f"    {n}")


async def dump_and_char(page, label, summary, key):
    html = ""
    try: html = await page.content()
    except Exception: pass
    await dump(page, label)
    ch = await characterize(page)
    cap = captcha_markers(html, page.url)
    summary[key] = {"url": page.url, "size": len(html), "captcha": cap, "characterize": ch}
    print(json.dumps({"url": page.url, "captcha": cap, "framework": ch.get("framework"),
                      "inputs": ch.get("inputs"), "buttons": ch.get("buttons")[:12]}, indent=2)[:2800])
    return html


async def _solve_v2_checkbox(page, rec):
    """Click the reCAPTCHA v2 'I'm not a robot' checkbox and report whether it
    AUTO-RESOLVES headless (token populated, no image challenge) — the decisive
    feasibility test. Returns (token, challenged)."""
    token, challenged = "", False
    try:
        anchor = page.frame_locator("iframe[src*='api2/anchor']").locator("#recaptcha-anchor")
        await anchor.click(timeout=8000)
        # poll for token or a visible challenge (bframe) for ~12s
        for _ in range(24):
            token = await page.evaluate(
                "() => { const t=document.querySelector('textarea[name=g-recaptcha-response]');"
                " return t ? t.value : ''; }")
            if token:
                break
            # detect an opened image-challenge bframe (size>0 / visible)
            try:
                bf = page.locator("iframe[src*='api2/bframe']")
                if await bf.count() > 0 and await bf.first.is_visible():
                    box = await bf.first.bounding_box()
                    if box and box.get("height", 0) > 100:
                        challenged = True
            except Exception:
                pass
            await page.wait_for_timeout(500)
    except Exception as e:
        rec["steps"]["v2_error"] = str(e)[:120]
    rec["steps"]["v2_token_len"] = len(token)
    rec["steps"]["v2_challenged"] = challenged
    return token, challenged


async def probe_case(page, cn, rec):
    # reset to the search page each case (prior nav leaves us elsewhere)
    try:
        await page.goto(SEARCH, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    box = None
    for sel in ["input[id*='Case' i][type='text']", "input[name*='Case' i]",
                "input[placeholder*='case' i]", "input[id*='Number' i]"]:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                box = loc; rec["steps"]["box_sel"] = sel; break
        except Exception:
            continue
    if not box:
        rec["result"] = "NO_SEARCH_BOX — see B_search inventory"; return
    await box.click(timeout=6000); await box.fill(cn)

    # DECISIVE: does the v2 checkbox auto-resolve headless from the datacenter IP?
    token, challenged = await _solve_v2_checkbox(page, rec)
    if challenged and not token:
        rec["result"] = "V2_CHALLENGE_WALL — image challenge popped, no token (headless blocked)"
        await dump(page, f"C_captcha_{tag(cn)}")
        return
    if not token:
        rec["result"] = "V2_NO_TOKEN — checkbox did not resolve (likely walled)"
        await dump(page, f"C_captcha_{tag(cn)}")
        return

    for bsel in ["button:has-text('Search')", "input[type=submit]", "button[type=submit]"]:
        b = page.locator(bsel).first
        if await b.count() > 0 and await b.is_visible():
            try: await b.click(timeout=5000)
            except Exception: pass
            break
    try: await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception: pass
    await dump(page, f"C_results_{tag(cn)}")
    html = await page.content()
    rec["steps"]["results"] = {"url": page.url, "case_present": tag(cn) in re.sub(r"[^A-Za-z0-9]","",html).upper()}
    # try open the case link
    link = page.locator(f"a:has-text('{cn}')").first
    if await link.count() == 0:
        link = page.locator("table a[href*='Case'], a[href*='CaseDetail'], a[href*='/Cases/']").first
    if await link.count() > 0:
        try:
            await link.click(timeout=8000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await dump(page, f"D_detail_{tag(cn)}")
        det = await page.content()
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        (OUT/f"{tag(cn)}_detail.txt").write_text(body, encoding="utf-8")
        disc = await page.evaluate(r"""() => {
            const tabs=Array.from(document.querySelectorAll('table')).map(t=>{
              const h=(t.querySelector('thead tr')||t.rows[0]);
              const cols=h?Array.from(h.cells).map(c=>(c.innerText||'').replace(/\s+/g,' ').trim()):[];
              const rows=t.querySelector('tbody')?t.querySelector('tbody').rows.length:Math.max(0,t.rows.length-1);
              return {cols,rows};}).filter(t=>t.cols.length||t.rows);
            const bt=document.body.innerText||'';
            return {tables:tabs.slice(0,15),
              kw:(bt.match(/docket|register of actions|party|parties|disposition|events/gi)||[]).length};}""")
        rec["steps"]["detail"] = {"url": page.url,
            "case_present": tag(cn) in re.sub(r"[^A-Za-z0-9]","",det).upper(),
            "body_len": len(body), "discovery": disc}
        rec["result"] = "DETAIL_OK" if rec["steps"]["detail"]["case_present"] else "DETAIL_UNCLEAR"
    else:
        rec["result"] = "SEARCH_DONE_NO_DETAIL_LINK"


async def party_search(page, term, rec):
    box = None
    for sel in ["input[placeholder*='name' i]", "input[id*='Name' i]", "input[id*='Party' i]",
                "input[type='text']:visible"]:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                box = loc; rec["steps"]["box_sel"] = sel; break
        except Exception:
            continue
    if not box:
        rec["result"] = "NO_NAME_BOX"; return
    await box.click(timeout=6000); await box.fill(term)
    try: await box.press("Enter")
    except Exception: pass
    for bsel in ["button:has-text('Search')", "input[type=submit]"]:
        b = page.locator(bsel).first
        if await b.count() > 0 and await b.is_visible():
            try: await b.click(timeout=5000)
            except Exception: pass
            break
    try: await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception: pass
    await dump(page, f"P_{tag(term)}")
    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
    (OUT/f"party_{tag(term)}.txt").write_text(body, encoding="utf-8")
    ucns = sorted(set(re.findall(r"20\d{2}-C[AC]-\d{6}-O", body)))
    rec["steps"]["ucns"] = ucns
    rec["result"] = f"{len(ucns)} foreclosure UCNs"


if __name__ == "__main__":
    asyncio.run(main())
