"""
Duval CoreCMS — PARTY-NAME SEARCH PROBE (investigation, Phase-2 vocab).

NOT production. Throwaway. Goal: find Duval foreclosure cases with REAL
surplus-claim activity by searching the Tyler portal's own party-name index for
recovery-firm names — the 7 recently-sold cases had zero claim activity (claims
lag the sale 90+ days), so we source older claimed cases directly.

Two search levers (criteria form: c_LastNameTextBox + c_NameSearchOptionsDropDownList
Basic/full-text -> getCaseSearch(GUID,true)):
  1. GENERIC: Last Name = "SURPLUS" / "RECOVERY" / "FUNDING" -> any case whose
     party name contains the word = a recovery firm, regardless of identity.
  2. SPECIFIC: the 9 Broward known recovery firms -> do the SAME firms operate in
     Jacksonville? (cross-county check).

Phase A: run each party search, capture results + extract CA/CC foreclosure UCNs.
Phase B: open up to MAX_OPEN matched dockets (paste-full path) and save full text
         so we can read the real claim Description-column vocabulary.

Usage: python scripts/duval_party_search.py "SURPLUS,RECOVERY,FUNDING,PRIORITY SURPLUS,GET LIQUID FUNDING"
"""
import sys, re, json, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

LANDING = "https://core.duvalclerk.com/CoreCms.aspx?mode=PublicAccess"
REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = Path("data/samples/duval/party")
UCN_RE = re.compile(r"16-20\d{2}-C[AC]-\d{6}-[A-Z0-9]{3,4}-[A-Z]{2}", re.I)
MAX_OPEN = 12

DEFAULT_TERMS = [
    # generic full-text party-name levers
    "SURPLUS", "RECOVERY", "FUNDING",
    # Broward known recovery firms (cross-county check)
    "PRIORITY SURPLUS", "GET LIQUID FUNDING", "THE RECOVERY AGENTS", "AMERIFUND",
    "NEW BEGINNINGS TRUSTEE", "PRESTIGE PROCESSING", "CAPITAL CRAFTER",
    "HOME DEFENSE", "EVO RECOVERY",
]


async def settle_public_access(page):
    await page.goto(LANDING, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_function(
            "() => { const e=document.getElementById('c_AccessTypeLabel'); "
            "return e && /public access/i.test(e.innerText); }", timeout=30000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass


async def open_search_form(page):
    await page.evaluate("() => { if (typeof openCmsPage==='function') openCmsPage(); }")
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    try:
        await page.wait_for_selector("input[id^='c_LastNameTextBox_']", state="visible", timeout=25000)
    except Exception:
        pass


async def dump(page, label):
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        (OUT / f"{label}.html").write_text(await page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        await page.screenshot(path=str(OUT / f"{label}.png"), full_page=True)
    except Exception:
        pass


async def party_search(context, term: str) -> dict:
    rec = {"term": term, "ucns": [], "result_count_text": "", "captcha": False, "error": ""}
    page = await context.new_page()
    try:
        await settle_public_access(page)
        await open_search_form(page)
        ln = page.locator("input[id^='c_LastNameTextBox_']").first
        if await ln.count() == 0:
            rec["error"] = "no LastName field"
            return rec
        ln_id = await ln.get_attribute("id")
        guid = ln_id.replace("c_LastNameTextBox_", "")
        # Basic/full-text name option
        try:
            await page.locator(f"#c_NameSearchOptionsDropDownList_{guid}").select_option(value="BasicFull", timeout=5000)
        except Exception:
            pass
        await ln.click(timeout=8000)
        await ln.fill(term)
        # faithful trigger of Begin Search
        try:
            await page.evaluate("(g) => getCaseSearch(g, true)", guid)
        except Exception as e:
            rec["error"] = f"getCaseSearch: {str(e)[:80]}"
            try:
                await page.locator(f"#c_BeginSearchButton1_{guid}").click(timeout=5000)
            except Exception:
                pass
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        # getCaseSearch opens a SEPARATE results tab that first shows "Preparing a
        # new search window". Wait until that placeholder is GONE *and* the results
        # have materialised (a UCN, a 'no records' message, or a result count).
        try:
            await page.wait_for_function(
                r"""() => {
                    const t = document.body ? document.body.innerText : '';
                    if (/Preparing a new search window/i.test(t)) return false;
                    return /16-20\d{2}-C[AC]-\d{6}/.test(t)
                        || /no\s+(records|cases|results|matches|rows)/i.test(t)
                        || /\d+\s+(match|matches|result|results|record|records|cases?)\b/i.test(t)
                        || /search results/i.test(t);
                }""", timeout=35000)
        except Exception:
            pass
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        html = await page.content()
        low = body.lower()
        rec["captcha"] = "required captcha" in low
        rec["still_preparing"] = "preparing a new search window" in low
        m = re.search(r"(\d+)\s+(?:result|record|match|matches|case)s?\b", low)
        rec["result_count_text"] = (m.group(0) if m else
                                    ("no records" if re.search(r"no\s+(records|cases|results|matches)", low) else ""))
        ucns = []
        seen = set()
        for u in UCN_RE.findall(body) + UCN_RE.findall(html):
            U = u.upper()
            if U not in seen:
                seen.add(U); ucns.append(U)
        rec["ucns"] = ucns
        OUT.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", term)
        (OUT / f"search_{safe}.txt").write_text(body, encoding="utf-8")
        await dump(page, f"search_{safe}")
        return rec
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return rec
    finally:
        await page.close()


async def open_case(context, ucn: str) -> dict:
    tag = re.sub(r"[^A-Za-z0-9]", "", ucn).upper()
    rec = {"ucn": ucn, "result": "", "claim_lines": []}
    page = await context.new_page()
    try:
        await settle_public_access(page)
        await open_search_form(page)
        box = page.locator("input[id^='c_UcnEntryBox_']").first
        if await box.count() == 0:
            rec["result"] = "no UCN box"; return rec
        bid = await box.get_attribute("id")
        await box.click(timeout=8000)
        await box.fill(ucn)
        try:
            await page.evaluate("(b) => getCaseTabByUcnBoxId(b)", bid)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        try:
            await page.wait_for_function(
                "(w) => document.body && document.body.innerText.replace(/[^A-Za-z0-9]/g,'').toUpperCase().includes(w)",
                arg=tag, timeout=15000)
        except Exception:
            pass
        body = ""
        for fr in page.frames:
            try:
                t = await fr.evaluate("() => document.body ? document.body.innerText : ''")
                if tag in re.sub(r"[^A-Za-z0-9]", "", t).upper():
                    body = t; break
            except Exception:
                continue
        if not body:
            rec["result"] = "OPEN_UNCLEAR"; return rec
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{tag}_docket.txt").write_text(body, encoding="utf-8")
        # scan Description lines for claim/recovery vocabulary
        CLAIM = re.compile(r"surplus|interven|disburs|excess|claim to|motion for surplus|"
                           r"notice of appearance|assignment|unclaimed|escheat|recovery|funding", re.I)
        rec["claim_lines"] = [l.strip()[:160] for l in body.splitlines()
                              if CLAIM.search(l) and len(l.strip()) > 8][:30]
        rec["result"] = "OK"
        return rec
    except Exception as e:
        rec["result"] = f"EXC: {type(e).__name__}: {str(e)[:100]}"
        return rec
    finally:
        await page.close()


async def main():
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    terms = [t.strip() for t in arg.split(",") if t.strip()] if arg else DEFAULT_TERMS
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"terms": terms, "searches": [], "opened": []}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        # ── Phase A: party searches ──
        all_ucns = []
        for term in terms:
            ctx = await browser.new_context(viewport={"width": 1400, "height": 1200},
                                            ignore_https_errors=True, user_agent=REAL_UA)
            print(f"\n===== PARTY SEARCH: {term!r} =====")
            rec = await party_search(ctx, term)
            await ctx.close()
            summary["searches"].append(rec)
            print(f"  count={rec['result_count_text']!r} captcha={rec['captcha']} "
                  f"error={rec['error']!r} ucns={len(rec['ucns'])}")
            for u in rec["ucns"][:15]:
                print(f"     {u}")
            for u in rec["ucns"]:
                if u not in all_ucns:
                    all_ucns.append(u)

        print(f"\n===== UNIQUE FORECLOSURE UCNs FOUND: {len(all_ucns)} (opening up to {MAX_OPEN}) =====")

        # ── Phase B: open dockets ──
        for ucn in all_ucns[:MAX_OPEN]:
            ctx = await browser.new_context(viewport={"width": 1400, "height": 1400},
                                            ignore_https_errors=True, user_agent=REAL_UA)
            print(f"\n----- OPEN {ucn} -----")
            rec = await open_case(ctx, ucn)
            await ctx.close()
            summary["opened"].append(rec)
            print(f"  result={rec['result']} claim_lines={len(rec['claim_lines'])}")
            for l in rec["claim_lines"]:
                print(f"     {l}")

        await browser.close()

    (OUT / "party_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n===== SUMMARY =====")
    for r in summary["searches"]:
        print(f"  search {r['term']:<22} -> count={r['result_count_text']!r} ucns={len(r['ucns'])} "
              f"captcha={r['captcha']} err={r['error'][:40]!r}")
    print(f"  unique UCNs: {len(all_ucns)} | opened: {len(summary['opened'])}")


if __name__ == "__main__":
    asyncio.run(main())
