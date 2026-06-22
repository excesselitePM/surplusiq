"""
Duval CoreCMS (Tyler) — INVESTIGATION PROBE (headless browser).

NOT a production scraper. Not registered in core/dockets. Not wired to cron or
the dashboard. Throwaway investigation tooling to answer Phase-1 questions:

  1. What is core.duvalclerk.com/CoreCms.aspx?mode=PublicAccess — ASP.NET
     WebForms (__VIEWSTATE/postback), an API, or an SPA?
  2. CAPTCHA? reCAPTCHA v2 (challenge) or v3 (score)? Does it wall headless
     from a datacenter IP (Miami v3 passed; Broward had a non-CAPTCHA PUBLIC
     path) — or is there a public/disclaimer-gated path that passes?
  3. Navigation: case number -> docket events. What fields/tokens/detail-hop?
  4. Full docket capture: one page or paginated/lazy? entry count vs any total.

CAPTCHA-SOLVING IS OFF THE TABLE. If the only path runs through a v2 challenge,
the probe records captcha_blocked=True and moves on.

Run 1 is characterization-heavy: it dumps the framework signals, every
form/input/select/button/link, the reCAPTCHA markers, and a network log, so a
single Actions run reveals the DOM. Case search is best-effort on top of that.

Saves per-stage HTML + full-page screenshots + probe_summary.json to
data/samples/duval/ci/ and prints a structured summary to stdout.

Usage: python scripts/duval_investigate.py "16-2025-CA-005932-AXXX-MA,16-2025-CA-004483-AXXX-MA"
"""
import sys, re, json, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

LANDING = "https://core.duvalclerk.com/CoreCms.aspx?mode=PublicAccess"
REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = Path("data/samples/duval/ci")

# network requests we care about (recaptcha, API/XHR backends)
NET_INTEREST = re.compile(r"recaptcha|hcaptcha|captcha|\.asmx|\.svc|/api/|/json|GetDocket|"
                          r"Search|CaseDetail|graphql", re.I)


def tag_of(cn: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", cn).upper()


def captcha_markers(html: str, url: str) -> dict:
    low = html.lower()
    sitekey = ""
    m = re.search(r'data-sitekey=["\']([^"\']+)', html) or \
        re.search(r'render=([0-9A-Za-z_\-]{30,})', html) or \
        re.search(r'sitekey["\']?\s*[:=]\s*["\']([^"\']+)', html, re.I)
    if m:
        sitekey = m.group(1)
    # v3 loads api.js?render=<key> (invisible/score); v2 uses g-recaptcha div + anchor/bframe iframe
    v3_hint = "api.js?render=" in low or "grecaptcha.execute" in low
    v2_hint = ('class="g-recaptcha"' in low or "g-recaptcha" in low
               or "recaptcha/api2/anchor" in low or "recaptcha/api2/bframe" in low)
    return {
        "any_recaptcha": ("recaptcha" in low),
        "any_hcaptcha": ("hcaptcha" in low),
        "v3_score_hint": v3_hint,
        "v2_challenge_hint": v2_hint,
        "sitekey": sitekey,
        "url_has_captcha": "captcha" in url.lower(),
        "challenge_text": any(t in low for t in
                              ["verify you are human", "i'm not a robot", "i am not a robot",
                               "unusual traffic", "are you a robot", "press and hold"]),
    }


async def characterize(page) -> dict:
    """Dump framework + form/field/link inventory from the live DOM."""
    return await page.evaluate(
        r"""() => {
            const txt = el => (el && (el.innerText || el.value || '') || '').replace(/\s+/g,' ').trim().slice(0,80);
            const vs = document.querySelector('#__VIEWSTATE');
            const out = {
                title: document.title,
                url: location.href,
                framework: {
                    has_viewstate: !!vs,
                    viewstate_len: vs ? (vs.value || '').length : 0,
                    has_eventvalidation: !!document.querySelector('#__EVENTVALIDATION'),
                    has_aspnet_form: !!document.querySelector('form#aspnetForm, form[action*=".aspx"]'),
                    react_root: !!document.querySelector('#root, [data-reactroot]'),
                    angular: !!document.querySelector('[ng-app],[ng-version]'),
                    script_srcs: Array.from(document.querySelectorAll('script[src]'))
                        .map(s => s.src).filter(s => /recaptcha|tyler|core|bundle|main|app|vendor|jquery|kendo/i.test(s))
                        .slice(0,25),
                },
                forms: Array.from(document.querySelectorAll('form')).map(f => ({
                    id: f.id, name: f.name, action: f.action, method: f.method,
                })),
                inputs: Array.from(document.querySelectorAll('input,textarea')).map(i => ({
                    id: i.id, name: i.name, type: i.type, placeholder: i.placeholder,
                    aria: i.getAttribute('aria-label') || '', val: (i.value||'').slice(0,30),
                })).slice(0,60),
                selects: Array.from(document.querySelectorAll('select')).map(s => ({
                    id: s.id, name: s.name, opts: Array.from(s.options).slice(0,8).map(o=>o.value+':'+o.text.slice(0,20)),
                })).slice(0,20),
                buttons: Array.from(document.querySelectorAll('button,input[type=submit],input[type=button],a[onclick],[role=button]'))
                    .map(b => ({tag: b.tagName, id: b.id, text: txt(b),
                                onclick: (b.getAttribute('onclick')||'').slice(0,80),
                                href: (b.getAttribute('href')||'').slice(0,80)})).slice(0,40),
                links: Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({text: txt(a), href: (a.getAttribute('href')||'').slice(0,90)}))
                    .filter(a => a.text && !/^javascript:void/.test(a.href)).slice(0,40),
                body_text_head: (document.body.innerText||'').replace(/\s+/g,' ').trim().slice(0,600),
            };
            return out;
        }"""
    )


async def dump(page, label, tag):
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        html = await page.content()
        (OUT / f"{tag}_{label}.html").write_text(html, encoding="utf-8")
    except Exception as e:
        html = ""
        print(f"      (content failed: {e})")
    try:
        await page.screenshot(path=str(OUT / f"{tag}_{label}.png"), full_page=True)
    except Exception as e:
        print(f"      (screenshot failed: {e})")
    return html


async def try_accept_disclaimer(page):
    """Tyler PublicAccess often gates behind an 'I agree / Accept / Continue'
    button. Click the first plausible one if present. Returns what it clicked."""
    for sel in [
        "input[type=submit][value*='Accept' i]", "input[type=submit][value*='Agree' i]",
        "button:has-text('Accept')", "button:has-text('I Agree')", "button:has-text('Agree')",
        "button:has-text('Continue')", "a:has-text('Accept')", "a:has-text('I Agree')",
        "#cmdYes", "#btnContinue", "input[value*='Continue' i]",
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                try:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                        await loc.click(timeout=5000)
                except Exception:
                    await loc.click(timeout=5000)
                return sel
        except Exception:
            continue
    return ""


async def main():
    cases_arg = sys.argv[1] if len(sys.argv) > 1 else "16-2025-CA-005932-AXXX-MA"
    cases = [c.strip() for c in cases_arg.split(",") if c.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    netlog = []
    summary = {"landing": LANDING, "cases": cases, "results": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True, user_agent=REAL_UA,
        )
        context.on("request", lambda r: netlog.append(f"{r.method} {r.url[:160]}")
                   if NET_INTEREST.search(r.url) else None)

        page = await context.new_page()

        # ── STAGE A: landing characterization ──
        print("===== STAGE A: LANDING =====")
        nav_err = ""
        try:
            await page.goto(LANDING, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
        except Exception as e:
            nav_err = f"{type(e).__name__}: {str(e)[:160]}"
            print(f"  landing nav error: {nav_err}")

        land_html = await dump(page, "A_landing", "PORTAL")
        land_char = await characterize(page)
        land_cap = captcha_markers(land_html, page.url)
        summary["landing_result"] = {
            "nav_error": nav_err, "final_url": page.url, "html_size": len(land_html),
            "captcha": land_cap, "characterize": land_char,
        }
        print(json.dumps({"url": page.url, "captcha": land_cap,
                          "framework": land_char.get("framework"),
                          "inputs": land_char.get("inputs"),
                          "selects": land_char.get("selects"),
                          "buttons": land_char.get("buttons")}, indent=2)[:3500])

        # ── STAGE B: open Case Search (openCmsPage) + enumerate frames ──
        print("\n===== STAGE B: OPEN CASE SEARCH =====")
        # wait for the auto PublicLogin to settle (Login Status: Public Access)
        try:
            await page.wait_for_function(
                "() => /Public Access/i.test(document.body.innerText)", timeout=20000)
        except Exception:
            pass
        login_status = await page.evaluate(
            "() => { const e=document.getElementById('c_AccessTypeLabel'); "
            "return e ? e.innerText.trim() : ''; }")
        print(f"  login status label: {login_status!r}")

        # trigger the Case Search nav (faithful: same call as the onclick)
        try:
            await page.evaluate("() => { if (typeof openCmsPage==='function') openCmsPage(); }")
        except Exception as e:
            print(f"  openCmsPage() error: {e}")
        # the search window loads async ("Preparing a new search window")
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        # wait for the 'Preparing...' placeholder to be replaced by real content
        try:
            await page.wait_for_function(
                "() => { const t=document.body.innerText||''; "
                "return !/Preparing a new search window/i.test(t) || "
                "document.querySelectorAll('iframe').length>0; }", timeout=20000)
        except Exception:
            pass

        # enumerate all frames + characterize each (search form likely in an iframe)
        frames_info = []
        for fr in page.frames:
            try:
                finfo = {"url": fr.url, "name": fr.name}
                fchar = await fr.evaluate(
                    r"""() => ({
                        inputs: Array.from(document.querySelectorAll('input,textarea')).map(i=>({
                            id:i.id,name:i.name,type:i.type,placeholder:i.placeholder,
                            aria:i.getAttribute('aria-label')||''})).slice(0,40),
                        selects: Array.from(document.querySelectorAll('select')).map(s=>({
                            id:s.id,name:s.name,opts:Array.from(s.options).slice(0,10).map(o=>o.value+':'+o.text.slice(0,24))})).slice(0,15),
                        buttons: Array.from(document.querySelectorAll('button,input[type=submit],input[type=button],a[onclick],[role=button]'))
                            .map(b=>({tag:b.tagName,id:b.id,
                                      text:((b.innerText||b.value||'').replace(/\s+/g,' ').trim()).slice(0,40),
                                      onclick:(b.getAttribute('onclick')||'').slice(0,90)})).slice(0,30),
                        body_head:(document.body?document.body.innerText:'').replace(/\s+/g,' ').trim().slice(0,300),
                    })""")
                finfo["characterize"] = fchar
                frames_info.append(finfo)
            except Exception as e:
                frames_info.append({"url": getattr(fr, "url", "?"), "error": str(e)[:120]})
        await dump(page, "B_case_search", "PORTAL")
        summary["case_search"] = {
            "login_status": login_status,
            "frame_count": len(page.frames),
            "frames": frames_info,
        }
        print(json.dumps(frames_info, indent=2)[:4500])

        # ── STAGE C: best-effort case search for each case ──
        for cn in cases:
            tag = tag_of(cn)
            print(f"\n===== STAGE C: SEARCH {cn} =====")
            rec = {"case": cn, "tag": tag, "steps": {}}
            try:
                # PASTE-FULL path: "Enter a case number" -> c_UcnEntryBox_<GUID>
                # (GUID is per-tab; match by id prefix) -> Open Case button calls
                # getCaseTabByUcnBoxId(boxId). Single frame, no iframe.
                search_frame = page
                box = page.locator("input[id^='c_UcnEntryBox_']").first
                if await box.count() == 0:
                    rec["result"] = "NO_UCN_BOX — see case_search inventory"
                    summary["results"].append(rec)
                    print("  no UCN entry box found")
                    continue
                box_id = await box.get_attribute("id")
                rec["steps"]["ucn_box_id"] = box_id
                await box.click(timeout=5000)
                await box.fill(cn)
                # faithful trigger: call the page's own Open-Case handler
                try:
                    await page.evaluate("(bid) => getCaseTabByUcnBoxId(bid)", box_id)
                except Exception as e:
                    rec["steps"]["open_case_error"] = str(e)[:140]
                    # fallback: click the Open Case button
                    try:
                        await page.locator("input[id^='c_SubmitCaseLookupButton_']").first.click(timeout=5000)
                    except Exception:
                        pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                # wait for the case tab to populate (case number lands in the DOM)
                try:
                    await page.wait_for_function(
                        "(want) => document.body && "
                        "document.body.innerText.replace(/[^A-Za-z0-9]/g,'').toUpperCase().includes(want)",
                        arg=tag, timeout=15000)
                except Exception:
                    pass

                res_html = await dump(page, "C_search", tag)
                # search results may be in the frame; capture both frame + page text
                frame_present = False
                hits = {}
                try:
                    fr_html = await search_frame.content()
                    frame_present = tag in re.sub(r"[^A-Za-z0-9]", "", fr_html).upper()
                    (OUT / f"{tag}_C_searchframe.html").write_text(fr_html, encoding="utf-8")
                    hits = await search_frame.evaluate(
                        r"""(cn) => {
                            const want = cn.replace(/[^A-Za-z0-9]/g,'').toUpperCase();
                            const els = Array.from(document.querySelectorAll('a[href],[onclick],tr,td'));
                            const matches = els.filter(a => (a.innerText||'').replace(/[^A-Za-z0-9]/g,'').toUpperCase().includes(want))
                                .map(a => ({tag:a.tagName,text:(a.innerText||'').trim().slice(0,70),
                                            href:(a.getAttribute && a.getAttribute('href')||'').slice(0,90),
                                            onclick:(a.getAttribute && a.getAttribute('onclick')||'').slice(0,90)}));
                            return {result_matches: matches.slice(0,10),
                                    tables: document.querySelectorAll('table').length};
                        }""", cn)
                except Exception as e:
                    hits = {"frame_eval_error": str(e)[:120]}
                rec["steps"]["search"] = {
                    "final_url": page.url, "size": len(res_html),
                    "captcha": captcha_markers(res_html, page.url),
                    "case_present_frame": frame_present,
                    "hits": hits,
                }

                # Open Case lands directly on the case-detail tab — run docket
                # discovery unconditionally across all frames + persist full text.
                det_html = await dump(page, "D_detail", tag)
                best = {}
                for fr in page.frames:
                    try:
                        fr_html = await fr.content()
                        if tag not in re.sub(r"[^A-Za-z0-9]", "", fr_html).upper():
                            continue
                        (OUT / f"{tag}_D_detailframe.html").write_text(fr_html, encoding="utf-8")
                        # persist the full visible text for vocabulary ground-truth
                        body_txt = await fr.evaluate("() => document.body ? document.body.innerText : ''")
                        (OUT / f"{tag}_D_detail.txt").write_text(body_txt, encoding="utf-8")
                        disc = await fr.evaluate(
                            r"""() => {
                                const tabs = Array.from(document.querySelectorAll('table')).map(t => {
                                    const head=(t.querySelector('thead tr')||t.rows[0]);
                                    const cols=head?Array.from(head.cells).map(c=>(c.innerText||'').replace(/\s+/g,' ').trim()):[];
                                    const body=t.querySelector('tbody')?t.querySelector('tbody').rows.length:Math.max(0,t.rows.length-1);
                                    return {cols, rows: body};
                                }).filter(t => t.cols.length || t.rows);
                                const pager=Array.from(document.querySelectorAll('[class*=pager],[class*=Pager],.k-pager-info'))
                                    .map(p=>(p.innerText||'').trim().slice(0,80)).filter(Boolean);
                                const bt=document.body.innerText||'';
                                return {frame_url: location.href, tables: tabs.slice(0,20), pager,
                                        docket_word_hits:(bt.match(/docket|register of actions|case events|filings|dockets/gi)||[]).length,
                                        body_len: bt.length};
                            }""")
                        best = disc
                        break
                    except Exception:
                        continue
                rec["steps"]["detail"] = {"docket_discovery": best, "case_present": bool(best)}
                rec["result"] = "DETAIL_OK" if best else "OPEN_UNCLEAR — see D_detail artifacts"

                # reset: re-open Case Search for the next case
                try:
                    await page.goto(LANDING, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_function(
                        "() => /Public Access/i.test(document.body.innerText)", timeout=15000)
                    await page.evaluate("() => { if (typeof openCmsPage==='function') openCmsPage(); }")
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
            except Exception as e:
                rec["result"] = f"EXCEPTION: {type(e).__name__}: {str(e)[:160]}"
                await dump(page, "ERROR", tag)
            summary["results"].append(rec)
            print(json.dumps(rec, indent=2)[:3500])

        await browser.close()

    summary["netlog_interesting"] = netlog[:60]
    (OUT / "probe_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n===== SUMMARY =====")
    lr = summary.get("landing_result", {})
    print(f"  landing url      : {lr.get('final_url')}")
    print(f"  landing captcha  : {lr.get('captcha')}")
    fw = lr.get("characterize", {}).get("framework", {})
    print(f"  framework        : {fw}")
    if "after_accept" in summary:
        print(f"  after_accept url : {summary['after_accept'].get('final_url')}")
    for r in summary["results"]:
        print(f"  {r['case']:<26} -> {r.get('result')}")
    print(f"\n  interesting network requests ({len(netlog)}):")
    for n in netlog[:25]:
        print(f"    {n}")


if __name__ == "__main__":
    asyncio.run(main())
