"""
Broward CaseSearchECA — INVESTIGATION PROBE (headless browser).

NOT a production scraper. Not registered in core/dockets. Not wired to cron or
the dashboard. Throwaway investigation tooling to answer two questions:
  1. Does the non-CAPTCHA PUBLIC path (CaseNumberSearchResultsPUBLIC) return data
     headless from a datacenter IP without redirecting into the v2 reCAPTCHA?
  2. Does the docket-DETAIL hop (ViewDetails -> CaseDetailViewer -> GetCaseDetail)
     work in a real browser session (it 302'd to an ASP.NET error under bare curl)?

CAPTCHA-SOLVING IS OFF THE TABLE. If the only path to detail runs through the v2
challenge, the probe records captcha_blocked=True for that case and moves on.

Saves per-stage HTML + full-page screenshots to data/samples/broward/ci/ and
prints a structured summary to stdout (read it from the Actions log).

Usage: python scripts/broward_investigate.py "CACE-13-021361,CACE-24-010415"
"""
import os, sys, re, json, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "https://www.browardclerk.org/Web2/CaseSearchECA"
LANDING = f"{BASE}/Index/?AccessLevel=ANONYMOUS"
REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
OUT = Path("data/samples/broward/ci")

# Full-page screenshots of 200+-row docket pages are multi-MB and slow; only take
# them when SHOTS=1. For the surplus-vocabulary hunt the HTML + extracted docket
# JSON is the evidence — screenshots are noise.
SHOTS = os.environ.get("SHOTS", "0") == "1"

# Surplus-vocabulary trigger substrings (case-insensitive, matched against the
# Description + Additional Text columns). Deliberately BROAD — investigation wants
# to SEE every candidate row and lift the verbatim string, not pre-judge it. False
# positives ('claim of lien', 'counterclaim') get sorted out by eye in the report.
SURPLUS_TRIGGERS = [
    "surplus", "disburs", "disbursement", "excess fund", "remaining fund",
    "unclaimed", "claim", "petition", "assignment", "stipulation",
    "notice of appearance", "appear",
]


def nodash(cn: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", cn).upper()


def captcha_markers(html: str, url: str) -> dict:
    low = html.lower()
    return {
        "url_has_CAPTCHA": "captcha" in url.lower(),
        "recaptcha_iframe": ("recaptcha/api2/anchor" in low or "recaptcha/api2/bframe" in low),
        "challenge_text": any(t in low for t in
                              ["verify you are human", "i'm not a robot",
                               "i am not a robot", "unusual traffic", "are you a robot"]),
        "aspx_error": "aspxerrorpath" in url.lower() or "/web2/error" in url.lower(),
    }


async def dump(page, label, case_tag):
    OUT.mkdir(parents=True, exist_ok=True)
    html = await page.content()
    (OUT / f"{case_tag}_{label}.html").write_text(html, encoding="utf-8")
    if SHOTS:
        try:
            await page.screenshot(path=str(OUT / f"{case_tag}_{label}.png"), full_page=True)
        except Exception as e:
            print(f"      (screenshot failed: {e})")
    return html


async def extract_docket(page) -> dict:
    """Parse the docket events table off the detail page.

    The detail page renders plain static HTML tables (no Kendo grid). The docket
    table is the one whose header row carries 'Description' + 'Additional Text'.
    Returns {found, header, count, rows:[{date,description,additional}]}.
    """
    return await page.evaluate(
        r"""() => {
            const norm = s => (s||'').replace(/ /g,' ').replace(/\s+/g,' ').trim();
            const tables = Array.from(document.querySelectorAll('table'));
            const merged = [];
            let header = null;
            for (const t of tables) {
                // header = explicit thead row, else first row
                const headRow = t.querySelector('thead tr') || t.rows[0];
                if (!headRow) continue;
                const cells = Array.from(headRow.cells).map(c => norm(c.innerText).toLowerCase());
                const de = cells.findIndex(h => h.includes('description'));
                if (de === -1) continue;
                const di = cells.findIndex(h => h.includes('date'));
                const ad = cells.findIndex(h => h.includes('additional'));
                if (ad === -1) continue;            // require the Additional Text column
                if (!header) header = cells;
                // body rows: prefer tbody, exclude the header row itself
                const body = t.querySelector('tbody')
                    ? Array.from(t.querySelector('tbody').querySelectorAll('tr'))
                    : Array.from(t.rows).slice(1);
                for (const r of body) {
                    const c = Array.from(r.cells).map(x => norm(x.innerText));
                    if (!c.length) continue;
                    const desc = de >= 0 ? (c[de]||'') : '';
                    const addl = ad >= 0 ? (c[ad]||'') : '';
                    if (!desc && !addl) continue;
                    merged.push({
                        date: di >= 0 ? (c[di]||'') : '',
                        description: desc,
                        additional: addl,
                    });
                }
            }
            return { found: merged.length > 0, header, count: merged.length, rows: merged };
        }"""
    )


def scan_surplus(rows):
    """Return docket rows whose Description or Additional Text hits a trigger."""
    hits = []
    for r in rows:
        blob = f"{r.get('description','')} {r.get('additional','')}".lower()
        matched = [t for t in SURPLUS_TRIGGERS if t in blob]
        if matched:
            hits.append({**r, "_triggers": sorted(set(matched))})
    return hits


async def probe_case(context, cn: str) -> dict:
    tag = nodash(cn)
    rec = {"case": cn, "nodash": tag, "stages": {}}
    page = await context.new_page()
    try:
        # ── 1. landing (session) ──
        await page.goto(LANDING, wait_until="domcontentloaded", timeout=45000)

        # ── 2. PUBLIC search ──
        search_url = f"{BASE}/CaseNumberSearchResultsPUBLIC?CaseNum={tag}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        results_html = await dump(page, "1_results", tag)
        rec["stages"]["results"] = {
            "final_url": page.url,
            "size": len(results_html),
            "captcha": captcha_markers(results_html, page.url),
            "case_present": tag in re.sub(r"[^A-Za-z0-9]", "", results_html).upper(),
        }

        # ── locate the rendered detail link. CRITICAL: the grid row binds
        #    ViewDetails(`#=Viewer#`) where `Viewer` is a ~152-char encrypted
        #    blob rendered into the row's onclick — NOT the short CaseID token.
        #    (The first probe passed CaseID and got an ASP.NET error.) ──
        trig = await page.evaluate(
            r"""() => {
                const links = Array.from(document.querySelectorAll('[onclick*="ViewDetails"]'));
                for (const a of links) {
                    const oc = a.getAttribute('onclick') || '';
                    const m = oc.match(/ViewDetails\(`([^`]+)`\)/);
                    if (m && m[1].indexOf('#=') === -1) {
                        return {token: m[1], text: (a.innerText||'').trim()};
                    }
                }
                return null;
            }"""
        )
        rec["stages"]["results"]["viewer_token_len"] = (len(trig["token"]) if trig else 0)
        rec["stages"]["results"]["link_text"] = (trig.get("text") if trig else None)

        if not trig:
            rec["result"] = "NO_VIEWER_LINK — no rendered ViewDetails link (see results HTML)"
            return rec

        # ── 3. detail hop: CLICK the real rendered link (faithful user action,
        #    correct Viewer token). The click submits #dynamicViewCaseDetail. ──
        link = page.locator('button.bc-casedetail-viewer, [onclick*="ViewDetails"]').first
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await link.click(timeout=10000)
        except Exception as e:
            rec["stages"]["detail_nav_error"] = str(e)[:200]
            # Fallback: call ViewDetails with the extracted Viewer token directly.
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                    await page.evaluate("(t) => ViewDetails(t)", trig["token"])
            except Exception as e2:
                rec["stages"]["detail_nav_error2"] = str(e2)[:200]
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        # Force any Kendo docket grid to load ALL rows (Miami lazy-load lesson):
        # bump pageSize and record the dataSource total so a paged/lazy docket
        # fully renders before capture, and we can cross-check entry count.
        try:
            totals = await page.evaluate(
                """() => {
                    const out=[];
                    if(!window.jQuery) return out;
                    jQuery('.k-grid').each(function(){
                        const g = jQuery(this).data('kendoGrid');
                        if(g && g.dataSource){
                            const total = g.dataSource.total();
                            try{ g.dataSource.pageSize(100000); }catch(e){}
                            out.push({id:this.id||'(noid)', total:total});
                        }
                    });
                    return out;
                }"""
            )
            rec["stages"]["detail_grid_totals"] = totals
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception as e:
            rec["stages"]["detail_expand_error"] = str(e)[:160]

        detail_html = await dump(page, "2_detail", tag)
        cap = captcha_markers(detail_html, page.url)
        rec["stages"]["detail"] = {
            "final_url": page.url,
            "size": len(detail_html),
            "captcha": cap,
            "case_present": tag in re.sub(r"[^A-Za-z0-9]", "", detail_html).upper(),
        }

        # ── 4. docket discovery on the detail page ──
        info = await page.evaluate(
            """() => {
                const out = {grids: [], tables: 0, pager_text: [], docket_words: 0};
                out.tables = document.querySelectorAll('table').length;
                document.querySelectorAll('.k-grid').forEach(g => {
                    const rows = g.querySelectorAll('tbody tr.k-master-row, tbody tr').length;
                    out.grids.push({id: g.id || '(noid)', rows});
                });
                document.querySelectorAll('.k-pager-info, .k-pager-sizes, .pager, [class*=pager]').forEach(p=>{
                    const t=(p.innerText||'').trim(); if(t) out.pager_text.push(t.slice(0,80));
                });
                out.docket_words = (document.body.innerText.match(/docket/gi)||[]).length;
                return out;
            }"""
        )
        rec["stages"]["detail"]["docket_discovery"] = info

        # ── 5. docket extraction + surplus-vocabulary scan ──
        try:
            docket = await extract_docket(page)
        except Exception as e:
            docket = {"found": False, "error": str(e)[:160], "rows": []}
        rows = docket.get("rows", [])
        hits = scan_surplus(rows)
        rec["stages"]["detail"]["docket_rows"] = docket.get("count", 0)
        rec["stages"]["detail"]["docket_header"] = docket.get("header")
        rec["surplus_hits"] = hits
        # persist the FULL docket so verbatim strings survive the run
        (OUT / f"{tag}_docket.json").write_text(
            json.dumps({"case": cn, "header": docket.get("header"),
                        "count": docket.get("count", 0), "rows": rows},
                       indent=2, ensure_ascii=False), encoding="utf-8")
        if hits:
            print(f"      *** {len(hits)} SURPLUS-VOCAB ROW(S) in {cn}:")
            for h in hits:
                print(f"        [{h.get('date','')}] DESC={h.get('description','')!r}")
                if h.get("additional"):
                    print(f"                 ADDL={h.get('additional','')!r}")
                print(f"                 triggers={h.get('_triggers')}")

        if cap["aspx_error"]:
            rec["result"] = "DETAIL_ERROR — landed on ASP.NET error page"
        elif cap["url_has_CAPTCHA"] or cap["recaptcha_iframe"] or cap["challenge_text"]:
            rec["result"] = "CAPTCHA_BLOCKED on detail hop"
        elif rec["stages"]["detail"]["case_present"]:
            rec["result"] = "DETAIL_OK — reached case detail page headless"
        else:
            rec["result"] = "DETAIL_UNCLEAR — see HTML/screenshot"
        return rec
    except Exception as e:
        rec["result"] = f"EXCEPTION: {type(e).__name__}: {str(e)[:200]}"
        try:
            await dump(page, "ERROR", tag)
        except Exception:
            pass
        return rec
    finally:
        await page.close()


async def main():
    cases_arg = sys.argv[1] if len(sys.argv) > 1 else "CACE-13-021361,CACE-24-010415"
    cases = [c.strip() for c in cases_arg.split(",") if c.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"cases": cases, "results": []}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True, user_agent=REAL_UA,
        )
        for cn in cases:
            print(f"\n===== PROBING {cn} =====")
            rec = await probe_case(context, cn)
            summary["results"].append(rec)
            print(json.dumps(rec, indent=2)[:4000])
        await browser.close()
    (OUT / "probe_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n===== SUMMARY =====")
    for r in summary["results"]:
        st = r.get("stages", {})
        nrows = st.get("detail", {}).get("docket_rows", 0)
        nhits = len(r.get("surplus_hits", []))
        print(f"  {r['case']:<18} -> {r.get('result')}  [docket_rows={nrows}, surplus_hits={nhits}]")

    print("\n===== SURPLUS-VOCAB HITS (verbatim) =====")
    any_hit = False
    for r in summary["results"]:
        hits = r.get("surplus_hits", [])
        if not hits:
            continue
        any_hit = True
        print(f"\n  ## {r['case']} — {len(hits)} row(s)")
        for h in hits:
            print(f"    [{h.get('date','')}] DESC={h.get('description','')!r}")
            if h.get("additional"):
                print(f"               ADDL={h.get('additional','')!r}")
            print(f"               triggers={h.get('_triggers')}")
    if not any_hit:
        print("  (no surplus-vocabulary rows found in any probed case)")


if __name__ == "__main__":
    asyncio.run(main())
