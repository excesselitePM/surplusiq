"""
THROWAWAY Montgomery decree probe (Phase 1 — investigate, no wiring).

Question: what is Montgomery's ACTUAL judgment-decree phrasing for principal +
interest rate + from-date? Does it match the Summit parser family
("due Plaintiff on the Note $X"), the Cuyahoga family ("sum of $X plus
interest" / "interest-bearing principal balance of $X"), or neither?

Reuses MontgomeryDocketScraper's full navigation (disclaimer → search → case →
Docket subscreen) and overrides ONLY the judgment-PDF step to:
  • save the FULL decree text (first 15 pages) to data/samples/montgomery/ci/,
  • print the phrasing evidence in the Actions log (head of text + context
    around every dollar figure),
  • run all three extractors side-by-side (old max()-near-keyword, Summit
    parse_oh_mortgage_debt, Cuyahoga parse_cuyahoga_mortgage_debt) and print
    the comparison. NO production behavior changes; nothing is wired.

Usage: python scripts/montgomery_decree_probe.py "2025 CV 06699,2026 CV 01226"
"""
import sys, re, io, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.dockets.montgomery import (
    MontgomeryDocketScraper, extract_debt_from_pdf_bytes, BASE_URL,
)
from core.dockets.oh_debt import (
    parse_oh_mortgage_debt, parse_cuyahoga_mortgage_debt, is_oh_tax_decree,
)

OUT = Path("data/samples/montgomery/ci")

# Diverse sample across the 35 historically-scraped Montgomery cases:
#   2025 CV 06699 — PennyMac, $189,442.88 extracted (standard bank mortgage)
#   2026 CV 01226 — Rocket Mortgage, $121,432.83 (2026 filing, freshest format)
#   2026 CV 02014 — Planet Home Lending, $136,850.06
#   2025 CV 02213 — Nationstar, $158,051.96 (classified yellow — live-lead shape)
#   2024 CV 05466 — UMB Bank, $1,716.87 ← SUSPICIOUS: almost certainly the old
#                   extractor grabbing a fee/cost line, not the judgment
#   2025 CV 02260 — Fox Ridge HOA, $9,155.35 (HOA lien foreclosure — likely a
#                   different decree shape than bank mortgages)
DEFAULT_CASES = [
    "2025 CV 06699",
    "2026 CV 01226",
    "2026 CV 02014",
    "2025 CV 02213",
    "2024 CV 05466",
    "2025 CV 02260",
]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "")


def _print_dollar_contexts(full_text: str, limit: int = 10) -> None:
    """Print ±300 chars of normalized text around every $ figure ≥ $1,000 —
    the phrasing evidence Phase 1 exists to capture."""
    norm = _norm(full_text)
    seen = 0
    for m in re.finditer(r"\$\s*[\d,]+(?:\.\d{2})?", norm):
        try:
            amt = float(m.group(0).replace("$", "").replace(",", "").strip())
        except ValueError:
            continue
        if amt < 1000:
            continue
        seen += 1
        if seen > limit:
            print(f"      … more $ figures omitted")
            break
        a, b = max(0, m.start() - 300), min(len(norm), m.end() + 300)
        print(f"      ── ${amt:,.2f} ──")
        print(f"      …{norm[a:b]}…")


def _report_parsers(full_text: str) -> None:
    print(f"      [tax-decree content check] is_oh_tax_decree = {is_oh_tax_decree(full_text)}")
    for name, fn in (("SUMMIT parse_oh_mortgage_debt", parse_oh_mortgage_debt),
                     ("CUYAHOGA parse_cuyahoga_mortgage_debt", parse_cuyahoga_mortgage_debt)):
        d = fn(full_text)
        print(f"      [{name}]")
        print(f"         principal=${d.principal:,.2f}  rate={d.interest_rate}  "
              f"from={d.interest_from_date}  base=${d.interest_base:,.2f}  "
              f"junior=${d.junior_liens:,.2f}  computable={d.has_computable_interest}")
        for n in d.notes:
            print(f"         note: {n}")


def extract_debt_from_pdf_bytes_from_text(full_text: str):
    """Text-level replay of the old extractor's selection (same keywords/logic),
    so the comparison uses identical text without re-parsing the PDF."""
    text_lower = full_text.lower()
    keywords = ["judgment", "decree", "amount due", "principal", "total amount",
                "sum of", "awarded", "ordered to pay", "indebtedness",
                "balance due", "amount owing"]
    qualified = []
    for match in re.finditer(r"\$\s*([\d,]+(?:\.\d{2})?)", full_text):
        try:
            amt = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if amt < 1000:
            continue
        context = text_lower[max(0, match.start() - 500):match.end()]
        if any(kw in context for kw in keywords):
            qualified.append((amt, match.start(), match.end()))
    if not qualified:
        return None, ""
    amt, ms, me = max(qualified, key=lambda x: x[0])
    return amt, full_text[max(0, ms - 200):me + 200]


class ProbeScraper(MontgomeryDocketScraper):

    async def _extract_judgment_from_pdf(self, page, result):
        """Same candidate selection + fetch as production, but SAVE full text
        for every fetched decree and print the three-way parser comparison."""
        case_id = getattr(self, "_active_case_id", "")
        rows = await page.query_selector_all("#tblDocketBody td.docketRows")
        print(f"      → docket rows found: {len(rows)} (case_id={case_id or 'unknown'})")

        judgment_doc_phrases = [
            "judgment entry and foreclosure decree",
            "judgment and decree of foreclosure",
            "judgment entry of foreclosure",
            "judgment and decree",
            "decree of foreclosure",
            "decree in foreclosure",
            "foreclosure decree",
            "final judgment entry",
            "final judgment",
            "final entry",
            "judgment entry",
            "entry of judgment",
            "magistrate's decision",
            "magistrate decision",
        ]
        exclusion_markers = [
            "fee", "cost statement", "clerk fee", "deposit", "refund",
            "release of judgment", "release of lien", "release of liens",
            "partial release", "satisfaction of judgment", "satisfaction",
            "transcript", "subpoena", "praecipe", "writ of execution",
            "garnishment",
        ]
        supporting_doc_markers = [
            "motion:", "motion for", "affidavit", "notice:", "notice of filing",
            "memorandum", "brief in", "reply in support", "exhibit",
        ]

        candidates = []
        for row in rows:
            try:
                row_id = await row.get_attribute("id") or ""
                m = re.match(r"docket_row_(\d+)", row_id)
                if not m:
                    continue
                row_text = (await row.inner_text()).strip()
                tl = row_text.lower()
                if any(ex in tl for ex in exclusion_markers):
                    continue
                prio = next((p for p, ph in enumerate(judgment_doc_phrases) if ph in tl), None)
                if prio is None or any(s in tl for s in supporting_doc_markers):
                    continue
                candidates.append((prio, m.group(1), row_text[:160].replace("\n", " ")))
            except Exception:
                continue
        candidates.sort(key=lambda c: c[0])
        print(f"      → judgment candidates: {len(candidates)}")
        for prio, did, prev in candidates[:6]:
            print(f"         · prio={prio} docketid={did} :: {prev}")

        if not candidates or not case_id:
            print(f"      → nothing to fetch (candidates={len(candidates)}, case_id={case_id!r})")
            return

        OUT.mkdir(parents=True, exist_ok=True)
        case_slug = re.sub(r"[^A-Za-z0-9]+", "_", result.case_number).strip("_")
        req = page.context.request
        fetched = 0
        for prio, docket_id, preview in candidates[:4]:
            url = (f"{BASE_URL}/Helpers/getDocumentFromOnBase.aspx"
                   f"?docketid={docket_id}&caseid={case_id}&documenttype=docket")
            try:
                resp = await req.get(url, timeout=20000)
                if not resp.ok:
                    print(f"      ⚠ PDF fetch {docket_id}: HTTP {resp.status}")
                    continue
                pdf_bytes = await resp.body()
                if len(pdf_bytes) < 1000 or pdf_bytes[:4] != b"%PDF":
                    print(f"      ⚠ PDF {docket_id}: not a PDF ({len(pdf_bytes)}b, {pdf_bytes[:8]!r})")
                    continue

                import pdfplumber
                full_text = ""
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    for pg in pdf.pages[:15]:
                        full_text += (pg.extract_text() or "") + "\n"

                out_file = OUT / f"{case_slug}_docket{docket_id}.txt"
                out_file.write_text(full_text, encoding="utf-8")
                fetched += 1
                print(f"\n      ✅ saved decree TEXT ({len(full_text)} chars) → {out_file}")
                print(f"      ══ ROW: {preview}")
                print(f"      ══ HEAD (first 1800 chars, normalized) ══")
                print(f"      {_norm(full_text)[:1800]}")
                print(f"      ══ DOLLAR-FIGURE CONTEXTS ══")
                _print_dollar_contexts(full_text)
                print(f"      ══ PARSER COMPARISON ══")
                old_amt, old_snip = extract_debt_from_pdf_bytes_from_text(full_text)
                print(f"      [OLD max()-near-keyword] amount="
                      f"{f'${old_amt:,.2f}' if old_amt else 'None'}")
                if old_snip:
                    print(f"         snippet: …{_norm(old_snip)}…")
                _report_parsers(full_text)

                # mirror production's stop-at-first-successful-extract behavior
                # so the per-case summary line stays comparable
                amount, _ = extract_debt_from_pdf_bytes(pdf_bytes)
                if amount and not result.prayer_amount:
                    result.prayer_amount = amount
                    result.debt_source = f"pdf_extract:docket_{docket_id}:judgment"
                if fetched >= 2:
                    break
            except Exception as e:
                print(f"      ⚠ PDF fetch {docket_id} failed: {type(e).__name__}: {e}")

        if not fetched:
            print(f"      → no decree PDF fetched for {result.case_number}")


async def main():
    cases = ([c.strip() for c in sys.argv[1].split(",") if c.strip()]
             if len(sys.argv) > 1 else DEFAULT_CASES)
    print(f"Montgomery decree probe — {len(cases)} cases: {cases}")
    s = ProbeScraper(headless=True)
    for cn in cases:
        print(f"\n{'=' * 20} {cn} {'=' * 20}")
        try:
            r = await s.scrape_case(cn)
            print(f"   done: old-extractor prayer=${r.prayer_amount:,.2f} "
                  f"src={r.debt_source} class={r.classification}")
        except Exception as e:
            print(f"   ❌ {cn}: {type(e).__name__}: {e}")

    saved = sorted(OUT.glob("*.txt")) if OUT.exists() else []
    print(f"\n📦 saved decree texts: {len(saved)}")
    for f in saved:
        print(f"   {f}")


if __name__ == "__main__":
    asyncio.run(main())
