"""
Lee Clerk (matrix.leeclerk.org) — THROWAWAY portal characterization probe.

NOT production. Investigation only. Answers, from the Actions datacenter IP with
a real headed-ish Chromium + real UA, the questions the local fetch could not
(matrix.leeclerk.org timed out, www.leeclerk.org returned 403 to bots):
  • portal vendor/product + framework (ASP.NET WebForms vs MVC, __VIEWSTATE)
  • CAPTCHA present? which kind (reCAPTCHA v2 checkbox / v3 score / hCaptcha)?
  • WAF/bot-wall (Cloudflare/Incapsula/Imperva/Akamai)?
  • is there an anonymous public search path, or is it login/terms gated?
This decides whether a FUTURE delayed-docket layer for Lee is even feasible —
it does NOT gate the PR-first lien build.

Saves HTML + screenshots + summary to data/samples/lee/ci/.
Usage: python scripts/lee_portal_probe.py
"""
import re, json, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "https://matrix.leeclerk.org"
CLERK = "https://www.leeclerk.org/services/court-records"
REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = Path("data/samples/lee/ci")
NET = re.compile(r"recaptcha|hcaptcha|captcha|/api/|cloudflare|incapsula|imperva|akamai|/Search|Case", re.I)


def captcha_markers(html):
    low = (html or "").lower()
    m = (re.search(r"render=([0-9A-Za-z_\-]{30,})", html or "")
         or re.search(r'data-sitekey=["\']([^"\']+)', html or ""))
    return {
        "any_recaptcha": "recaptcha" in low,
        "any_hcaptcha": "hcaptcha" in low,
        "v3_render": "api.js?render=" in low,
        "v2_checkbox": 'class="g-recaptcha"' in low or "g-recaptcha-response" in low,
        "sitekey": m.group(1) if m else "",
        "waf": [w for w in ("cloudflare", "incapsula", "imperva", "_incap_", "akamai", "distil")
                if w in low],
        "challenge_text": [t for t in
            ("i'm not a robot", "verify you are human", "unusual traffic",
             "press and hold", "checking your browser", "access denied", "terms of use",
             "i agree", "i accept")
            if t in low],
    }


async def characterize(page):
    return await page.evaluate(r"""() => {
        const vs=document.querySelector('#__VIEWSTATE');
        return {
          title: document.title, url: location.href,
          framework: {
            has_viewstate: !!vs,
            aspx_form: !!document.querySelector('form[action*=".aspx"]'),
            mvc_hint: /\/Cases?\/|\/Home\/|\/Account\/|\/Portal\//.test(document.body.innerHTML),
            react_root: !!document.querySelector('#root,[data-reactroot]'),
            generator: (document.querySelector('meta[name=generator]')||{}).content||'',
            scripts: Array.from(document.querySelectorAll('script[src]')).map(s=>s.src)
              .filter(s=>/recaptcha|tyler|odyssey|benchmark|bundle|jquery|app|main|portal/i.test(s)).slice(0,15)},
          inputs: Array.from(document.querySelectorAll('input,textarea,select')).map(i=>({
            id:i.id,name:i.name,type:i.type,placeholder:i.placeholder})).slice(0,30),
          links: Array.from(document.querySelectorAll('a[href]')).map(a=>({
            text:(a.innerText||'').replace(/\s+/g,' ').trim().slice(0,40),
            href:(a.getAttribute('href')||'').slice(0,90)}))
            .filter(l=>/search|case|record|portal|login|agree|accept/i.test(l.text+l.href)).slice(0,25),
          body_head:(document.body.innerText||'').replace(/\s+/g,' ').trim().slice(0,600)
        };}""")


async def dump(page, label):
    OUT.mkdir(parents=True, exist_ok=True)
    try: (OUT/f"{label}.html").write_text(await page.content(), encoding="utf-8")
    except Exception: pass
    try: await page.screenshot(path=str(OUT/f"{label}.png"), full_page=True)
    except Exception: pass


async def visit(page, url, label, summary, netlog):
    print(f"\n===== {label}: {url} =====")
    rec = {"url": url, "label": label}
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        rec["http_status"] = resp.status if resp else None
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
    except Exception as e:
        rec["nav_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"  NAV ERROR: {rec['nav_error']}")
        summary["results"].append(rec)
        return
    html = ""
    try: html = await page.content()
    except Exception: pass
    await dump(page, label)
    rec["size"] = len(html)
    rec["captcha"] = captcha_markers(html)
    rec["characterize"] = await characterize(page)
    summary["results"].append(rec)
    print(json.dumps({"http_status": rec.get("http_status"), "size": rec["size"],
                      "captcha": rec["captcha"],
                      "framework": rec["characterize"].get("framework"),
                      "title": rec["characterize"].get("title"),
                      "links": rec["characterize"].get("links")}, indent=2)[:3000])


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    netlog = []
    summary = {"target": BASE, "results": []}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 1100},
                                        ignore_https_errors=True, user_agent=REAL_UA)
        ctx.on("request", lambda r: netlog.append(f"{r.method} {r.url[:150]}")
               if NET.search(r.url) else None)
        page = await ctx.new_page()
        await visit(page, BASE + "/", "A_matrix_landing", summary, netlog)
        # Common Tyler/portal search routes — try whichever resolves.
        for path, label in [("/CourtRecords", "B_courtrecords"),
                            ("/Portal", "C_portal"),
                            ("/Home", "D_home"),
                            ("/Search", "E_search")]:
            await visit(page, BASE + path, label, summary, netlog)
        await visit(page, CLERK, "F_clerk_site", summary, netlog)
        await browser.close()

    summary["netlog"] = netlog[:60]
    (OUT/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n===== SUMMARY =====")
    for r in summary["results"]:
        cap = r.get("captcha", {})
        fw = (r.get("characterize") or {}).get("framework", {})
        print(f"  {r['label']:<18} http={r.get('http_status')} size={r.get('size')} "
              f"recaptcha={cap.get('any_recaptcha')} v2={cap.get('v2_checkbox')} "
              f"v3={cap.get('v3_render')} waf={cap.get('waf')} "
              f"viewstate={fw.get('has_viewstate')} mvc={fw.get('mvc_hint')} "
              f"nav_err={r.get('nav_error','')}")
    print(f"  netlog ({len(netlog)}):")
    for n in netlog[:25]:
        print(f"    {n}")


if __name__ == "__main__":
    asyncio.run(main())
