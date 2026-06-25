"""
THROWAWAY Cuyahoga decree probe (Phase 1 — investigate, no wiring).

Question: does Cuyahoga's cpdocket portal expose the judgment-DECREE document
(with the "$X principal ... Y% per annum from [date]" language oh_debt needs), or
only the structured "Prayer Amount" field the current scraper already reads?

Reuses CuyahogaDocketScraper's navigation (subclass override of _scrape_docket_page)
to reach each case's docket page, then:
  • inventories every document-ish link / image on the docket page,
  • for any Judgment/Decree/Foreclosure docket row with a link, fetches it and
    saves the decree TEXT to data/diagnostics/cuyahoga-oh/ for inspection.

Usage: python scripts/cuyahoga_decree_probe.py CV19923457,CV25115432,...
"""
import sys, re, json, io, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.dockets.cuyahoga import CuyahogaDocketScraper

OUT = Path("data/diagnostics/cuyahoga-oh")
HOST = "https://cpdocket.cp.cuyahogacounty.gov"


class ProbeScraper(CuyahogaDocketScraper):
    async def _scrape_docket_page(self, page, result):
        await super()._scrape_docket_page(page, result)
        OUT.mkdir(parents=True, exist_ok=True)
        case = re.sub(r"[^A-Za-z0-9]", "_", result.case_number)
        info = await page.evaluate(r"""() => {
            const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text:(a.innerText||'').replace(/\s+/g,' ').trim().slice(0,70),
                href:a.getAttribute('href')}));
            const docLinks = links.filter(l => /doc|image|pdf|tif|view|getfile|\.aspx\?/i.test(l.href||''));
            const imgs = Array.from(document.querySelectorAll('img'))
                .map(i=>i.getAttribute('src')).filter(s=>/doc|image|view|pdf|camera|icon|scan/i.test(s||'')).slice(0,25);
            const rows = Array.from(document.querySelectorAll('table tr')).map(tr => ({
                t:(tr.innerText||'').replace(/\s+/g,' ').trim().slice(0,130),
                a:Array.from(tr.querySelectorAll('a[href]')).map(x=>x.getAttribute('href')),
                im:Array.from(tr.querySelectorAll('img')).map(x=>x.getAttribute('src'))
            })).filter(r=>r.t).slice(0,80);
            return {total_links:links.length, docLinks:docLinks.slice(0,40), imgs, rows};
        }""")
        (OUT / f"{case}-docket-links.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        print(f"   📋 {result.case_number}: {len(info['docLinks'])} doc-ish links, "
              f"{len(info['imgs'])} doc images, {len(info['rows'])} docket rows")
        for l in info["docLinks"][:8]:
            print(f"        link: {l['text'][:40]!r} → {l['href'][:80]}")

        decree_rows = [r for r in info["rows"]
                       if re.search(r"judgment|decree|foreclosure|magistrate|final", r["t"], re.I)
                       and (r["a"] or r["im"])]
        print(f"   → {len(decree_rows)} judgment/decree rows WITH a link/image")
        for r in decree_rows[:4]:
            href = (r["a"] or [None])[0]
            if not href:
                print(f"        [{r['t'][:45]}] has image but NO href link")
                continue
            url = href if href.startswith("http") else f"{HOST}/{href.lstrip('/')}"
            try:
                resp = await page.context.request.get(url, timeout=20000)
                body = await resp.body()
                print(f"        FETCH [{r['t'][:40]}] → {resp.status} {len(body)}b head={body[:8]!r}")
                if body[:4] == b"%PDF":
                    import pdfplumber
                    txt = ""
                    with pdfplumber.open(io.BytesIO(body)) as pdf:
                        for pg in pdf.pages[:15]:
                            txt += (pg.extract_text() or "") + "\n"
                    (OUT / f"{case}-decree.txt").write_text(txt, encoding="utf-8")
                    print(f"        ✅ saved decree TEXT ({len(txt)} chars) → {case}-decree.txt")
                    break
            except Exception as e:
                print(f"        ⚠ fetch failed: {type(e).__name__}: {e}")


async def main():
    cases = (sys.argv[1].split(",") if len(sys.argv) > 1
             else ["CV19923457", "CV25115432", "CV25110566", "CV24106082", "CV25111605"])
    s = ProbeScraper(headless=True)
    for cn in cases:
        print(f"\n===== {cn} =====")
        try:
            r = await s.scrape_case(cn.strip())
            print(f"   prayer_field=${r.prayer_amount:,.2f}  class={r.classification}")
        except Exception as e:
            print(f"   ❌ {cn}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
