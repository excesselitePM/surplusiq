"""
Montgomery / OH-mortgage conservative debt extractor — ACCEPTANCE TEST.

THE GATE for the Montgomery oh_debt port (Phase 2). Runs the EXACT production
logic against 9 REAL committed Montgomery decree texts
(data/samples/montgomery/ci/*.txt, captured by probe runs 31449965664 +
31452640977) with their REAL sale dates + prices.

What it locks in:
  • Montgomery is the SUMMIT parser family — parse_oh_mortgage_debt anchors
    match the "due to Plaintiff on the promissory note … the sum of $X" and
    "principal balance/sum of $X" variants on all 6 mortgage decrees.
  • 2025 CV 06927 (was live as principal-only $45,388 "Verified") recomputes
    to a $34,361 conservative surplus — SURVIVES, smaller.
  • 2025 CV 02213 picks the $158,051.96 judgment; the $164,138.43 sale-proceeds
    DISTRIBUTION order parses to $0 (anchors refuse it) and its docket-row text
    hits the new exclusion markers.
  • HOA/junior-lien decrees (2025 CV 03200 — the $257K phantom-surplus bug —
    and 2025 CV 02260) anchor-miss in oh_debt AND trip the HOA lien detection:
    no confident surplus is possible (senior mortgage "to be determined").
  • The $10K plausibility floor rejects the $1,716.87 UMB small-note judgment
    (2024 CV 05466) — a junior note whose senior debt is unknown — even though
    oh_debt alone would happily compute a "surplus" from it.
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.dockets.oh_debt import oh_mortgage_debt, parse_oh_mortgage_debt
from core.dockets.montgomery import (
    _HOA_LIEN_ANCHOR, _HOA_LIEN_MARKERS, extract_debt_from_pdf_bytes,
)

SAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "data", "samples", "montgomery", "ci"))

_checks = []


def check(name, cond, detail=""):
    _checks.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def load(fname):
    with open(os.path.join(SAMPLES, fname), encoding="utf-8") as f:
        return f.read()


# (fixture, sale_date, sale_price, expected_verdict, expected_principal, label)
MORTGAGE_CASES = [
    ("2025_CV_06699_judgment.txt", date(2026, 7, 31),  80800.0, "killed",  189442.88, "PennyMac"),
    ("2026_CV_01226_judgment.txt", date(2026, 8, 7),   99800.0, "killed",  121432.83, "Rocket ('with interest' variant)"),
    ("2026_CV_02014_judgment.txt", date(2026, 8, 7),  126075.0, "killed",  136850.06, "Planet Home"),
    ("2025_CV_02213_judgment.txt", date(2026, 7, 10), 173100.0, "killed",  158051.96, "Nationstar"),
    ("2025_CV_06927_judgment.txt", date(2026, 7, 31), 115100.0, "surplus",  69711.60, "MyCUMortgage (live lead)"),
]


def main():
    print("=" * 78)
    print("  Montgomery OH-mortgage conservative debt extractor — acceptance")
    print("=" * 78)

    results = {}
    for fname, sd, sp, expected, principal, label in MORTGAGE_CASES:
        d = oh_mortgage_debt(load(fname), sd, sp)
        results[fname] = d
        print(f"\n{label}  ({fname})  sale ${sp:,.0f} on {sd}")
        print(f"   principal=${d.principal:,.2f}  rate="
              f"{(str(round(d.interest_rate*100,3))+'%') if d.interest_rate else 'none'}"
              f"  from={d.interest_from_date}")
        print(f"   + interest=${d.accrued_interest:,.0f}  + junior=${d.junior_liens:,.0f}"
              f"  + buffer=${d.buffer:,.0f}  = DEBT ${d.total_debt:,.0f}"
              f"  → surplus ${(d.surplus or 0):,.0f}")
        print(f"   VERDICT: {d.verdict.upper()}" + (f"  [{d.flag}]" if d.flag else ""))
        check(f"{label}: principal anchored to ${principal:,.2f}",
              abs(d.principal - principal) < 1, f"got ${d.principal:,.2f}")
        check(f"{label}: verdict == {expected}", d.verdict == expected, d.verdict)
        check(f"{label}: computable interest (rate + from-date parsed)",
              d.has_computable_interest)

    print("\n" + "-" * 78)

    # ── 2025 CV 06927 — the live "Verified" lead, recomputed honestly ──
    d6927 = results["2025_CV_06927_judgment.txt"]
    check("06927: conservative surplus $34,361 (was $45,388 principal-only)",
          abs(d6927.surplus - 34361.47) < 1, f"surplus=${d6927.surplus:,.2f}")
    check("06927: surplus survives (exceeds buffer)",
          d6927.verdict == "surplus" and d6927.surplus > d6927.buffer)

    # ── 2025 CV 02213 — distribution-order trap ──
    dist_text = load("2025_CV_02213_distribution.txt")
    p_dist = parse_oh_mortgage_debt(dist_text)
    check("02213 distribution order: oh_debt anchors REFUSE it (principal $0)",
          p_dist.principal == 0.0, f"principal=${p_dist.principal:,.2f}")
    legacy_amt = _legacy_max_from_text(dist_text)
    check("02213 distribution order: legacy max() WOULD have shipped $164,138.43 (the trap)",
          legacy_amt is not None and abs(legacy_amt - 164138.43) < 1,
          f"legacy=${legacy_amt:,.2f}" if legacy_amt else "none")
    from core.dockets import montgomery as _m
    src = open(_m.__file__, encoding="utf-8").read()
    check("distribution/confirmation rows are in the candidate exclusion markers",
          '"confirming sale"' in src and '"distribution"' in src and '"proceeds of sale"' in src)

    # ── HOA / junior-lien decrees — the $257K phantom-surplus class ──
    for fname, amt, label in [
        ("2025_CV_03200_hoa_judgment.txt", 5986.35, "Pheasant Ridge (the live $257K phantom)"),
        ("2025_CV_02260_hoa_judgment.txt", 9155.35, "Fox Ridge"),
    ]:
        text = load(fname)
        p = parse_oh_mortgage_debt(text)
        norm = re.sub(r"\s+", " ", text)
        hoa_m = _HOA_LIEN_ANCHOR.search(norm)
        markers = any(m in text.lower() for m in _HOA_LIEN_MARKERS)
        check(f"{label}: oh_debt anchors refuse the HOA figure (principal $0)",
              p.principal == 0.0, f"principal=${p.principal:,.2f}")
        check(f"{label}: HOA lien anchor + markers detect it (→ flagged, no surplus math)",
              hoa_m is not None and markers and abs(float(hoa_m.group(1).replace(",", "")) - amt) < 1,
              f"anchor=${hoa_m.group(1) if hoa_m else 'none'}, markers={markers}")
    check("03200 decree itself proves senior debt unknowable ('subject to the mortgage')",
          "sold subject to the mortgage" in load("2025_CV_03200_hoa_judgment.txt").lower())

    # ── $10K plausibility floor — 2024 CV 05466 ($1,716.87 junior note) ──
    p_umb = parse_oh_mortgage_debt(load("2024_CV_05466_judgment.txt"))
    check("05466: principal parsed at $1,716.87 (a junior small-note judgment)",
          abs(p_umb.principal - 1716.87) < 0.01, f"${p_umb.principal:,.2f}")
    check("05466: BELOW the $10K floor — scraper must skip it (0 < principal < 10000)",
          0 < p_umb.principal < 10000.0)

    # ── HOA anchor must NOT false-positive on real mortgage decrees ──
    fp = 0
    for fname, *_ in MORTGAGE_CASES:
        text = load(fname)
        if (_HOA_LIEN_ANCHOR.search(re.sub(r"\s+", " ", text))
                and any(m in text.lower() for m in _HOA_LIEN_MARKERS)
                and parse_oh_mortgage_debt(text).principal == 0.0):
            fp += 1
    check("HOA detection fires on ZERO of the 5 mortgage decrees", fp == 0, f"{fp} false positives")

    passed = sum(_checks)
    total = len(_checks)
    print("\n" + "=" * 78)
    print(f"  RESULT: {passed}/{total} checks passed")
    print("=" * 78)
    if passed != total:
        sys.exit(1)


def _legacy_max_from_text(full_text: str):
    """Text-level replay of the legacy max()-near-keyword selection, to document
    the exact figure the old extractor would have shipped."""
    tl = full_text.lower()
    kws = ["judgment", "decree", "amount due", "principal", "total amount",
           "sum of", "awarded", "ordered to pay", "indebtedness",
           "balance due", "amount owing"]
    hits = []
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d{2})?)", full_text):
        try:
            amt = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if amt >= 1000 and any(k in tl[max(0, m.start() - 500):m.end()] for k in kws):
            hits.append(amt)
    return max(hits) if hits else None


if __name__ == "__main__":
    main()
