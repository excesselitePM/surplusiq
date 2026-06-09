"""Phase 2 verification: full scrape_case (retrieve -> parse -> classify)
against the real Miami-Dade mortgage-foreclosure leads in docs/data/leads.json.

Reports each case's foreclosure_type / evidence_level / lead_status /
classification with the docket evidence (claim/kill/surplus titles) that drove
the decision.

Run:  PYTHONPATH=. python data/samples/miami_dade/_test_phase2.py
"""
import asyncio
import re

from core.dockets.miami_dade import MiamiDadeDocketScraper

# 6 mortgage-foreclosure cases (CA/CC) from docs/data/leads.json + the
# investigation baseline. (The two 2026A* tax-deed leads are out of scope.)
CASES = [
    "2024-020538-CA-01",   # ACCEPTANCE: Eric's $29,676 case — claim already filed -> MUST die
    "2017-021344-CA-01",   # baseline (had many motions + bankruptcy hits)
    "2025-095651-CC-25",
    "2025-000672-CA-01",
    "2019-001371-CA-01",
    "2019-009163-CC-05",
    "2025-010668-CA-01",
    "2025-012741-CA-01",
]

EVIDENCE_TERMS = re.compile(
    r"(surplus|claim|disburs|vacate|set aside|cancel|bankrupt|intervene)", re.I)


async def main():
    scraper = MiamiDadeDocketScraper(headless=True)
    results = []
    for cn in CASES:
        print(f"\n===== {cn} =====")
        r = await scraper.scrape_case(cn)
        # docket titles that look relevant (the evidence)
        ev_titles = [e["description"] for e in r.events
                     if EVIDENCE_TERMS.search(e["description"])]
        print(f"  caption        : {r.case_title[:70]!r}")
        print(f"  status/type    : {r.last_status!r} / {r.case_designation!r}")
        print(f"  foreclosure_type: {r.foreclosure_type}")
        print(f"  evidence_level : {r.evidence_level}")
        print(f"  lead_status    : {r.lead_status}")
        print(f"  classification : {r.classification}  ({r.classification_reason})")
        print(f"  claim_filed    : {r.claim_filed}  type={r.claim_type!r}")
        print(f"  kill_signals   : {r.kill_signals}")
        print(f"  competing      : {r.competing_filers}")
        print(f"  events(total)  : {len(r.events)}")
        if ev_titles:
            print(f"  EVIDENCE docket titles:")
            for t in ev_titles[:12]:
                print(f"     · {t}")
        results.append(r)

    print("\n\n================ SUMMARY ================")
    print(f"{'case':22} {'evidence_level':22} {'lead_status':22} {'class'}")
    for cn, r in zip(CASES, results):
        print(f"{cn:22} {r.evidence_level:22} {r.lead_status:22} {r.classification}")

    killed = [r for r in results if r.lead_status == "not_pursuable"]
    pursuable = [r for r in results if r.lead_status in ("pursuable", "pursuable_with_caution")]
    claim = [r for r in results if r.claim_filed]
    print(f"\nnot_pursuable: {len(killed)}   pursuable(+caution): {len(pursuable)}   claim_filed: {len(claim)}")
    print("ACCEPTANCE: at least one claim_filed->not_pursuable AND at least one clean->pursuable")
    print("  claim_filed->not_pursuable present:",
          any(r.claim_filed and r.lead_status == "not_pursuable" for r in results))
    print("  clean->pursuable present         :",
          any(r.evidence_level == "no_claim_found" and r.lead_status == "pursuable" for r in results))


if __name__ == "__main__":
    asyncio.run(main())
