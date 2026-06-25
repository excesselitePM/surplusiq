"""
Summit / OH-mortgage conservative debt extractor — ACCEPTANCE TEST.

THE GATE for the false-positive fix. Runs the EXACT production logic
(core/dockets/oh_debt.oh_mortgage_debt) against 5 REAL committed Summit judgment
decrees (data/samples/summit/ci/*_judgment.txt, captured by the investigation)
with their REAL sale dates + prices.

Each case asserts the verdict the client requires:
  6973 Van Buren  → KILLED  (debt ≥ sale; MUST NOT ship as +$32K)
  Salmon          → KILLED  (near-zero/negative after interest+buffer)
  Revere          → SURPLUS (genuine, stays green)
  Royal County Dn → FLAG    (junior lien added, no computable interest → uncertain)
  Canton          → TAX     (RC 5721 decree, routed away from mortgage logic)
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.dockets.oh_debt import oh_mortgage_debt, DEFAULT_BUFFER_PCT

SAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "data", "samples", "summit", "ci"))

_checks = []


def check(name, cond, detail=""):
    _checks.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def load(case):
    with open(os.path.join(SAMPLES, f"{case}_judgment.txt"), encoding="utf-8") as f:
        return f.read()


# (case, sale_date, sale_price, expected_verdict, label)
CASES = [
    ("CV2024125264", date(2026, 5, 15), 277300.0, "killed",         "6973 Van Buren"),
    ("CV2025115614", date(2026, 5, 15), 136800.0, "killed",         "Salmon"),
    ("CV2025052012", date(2026, 5, 15), 258100.0, "surplus",        "Revere"),
    ("CV2019062134", date(2026, 6, 12), 107300.0, "flag_uncertain", "Royal County Down"),
    ("CV2025105047", date(2026, 6, 2),  188700.0, "tax_decree",     "Canton (tax)"),
]


def main():
    print("=" * 78)
    print(f"  Summit OH-mortgage conservative debt extractor — acceptance "
          f"(buffer {DEFAULT_BUFFER_PCT*100:.0f}% of principal)")
    print("=" * 78)

    results = {}
    for case, sd, sp, expected, label in CASES:
        d = oh_mortgage_debt(load(case), sd, sp)
        results[case] = d
        print(f"\n{label}  ({case})  sale ${sp:,.0f} on {sd}")
        if d.is_tax_decree:
            print(f"   TAX DECREE — mortgage logic skipped")
        else:
            print(f"   principal=${d.principal:,.2f}  rate="
                  f"{(str(round(d.interest_rate*100,3))+'%') if d.interest_rate else 'none'}"
                  f"  from={d.interest_from_date}  base=${d.interest_base:,.2f}")
            print(f"   + interest=${d.accrued_interest:,.0f}  + junior=${d.junior_liens:,.0f}"
                  f"  + buffer=${d.buffer:,.0f}  = DEBT ${d.total_debt:,.0f}")
            print(f"   surplus = sale ${sp:,.0f} − debt ${d.total_debt:,.0f} = "
                  f"${(d.surplus if d.surplus is not None else 0):,.0f}")
        print(f"   VERDICT: {d.verdict.upper()}" + (f"  [{d.flag}]" if d.flag else ""))
        check(f"{label}: verdict == {expected}", d.verdict == expected, d.verdict)

    print("\n" + "-" * 78)

    # Targeted assertions on the headline false-positive and the correctness items.
    d6973 = results["CV2024125264"]
    check("6973: split-balance base is $138,242.97 (NOT full $244,898.61)",
          abs(d6973.interest_base - 138242.97) < 1, f"base=${d6973.interest_base:,.2f}")
    check("6973: does NOT ship a positive surplus (the false-positive)",
          d6973.surplus is not None and d6973.surplus <= 0, f"surplus=${d6973.surplus:,.0f}")

    rcd = results["CV2019062134"]
    check("Royal County Down: junior lien $13,028.40 added (not ignored)",
          abs(rcd.junior_liens - 13028.40) < 1, f"junior=${rcd.junior_liens:,.2f}")
    check("Royal County Down: principal anchored to $76,982.92",
          abs(rcd.principal - 76982.92) < 1, f"principal=${rcd.principal:,.2f}")

    rev = results["CV2025052012"]
    check("Revere: genuine surplus survives (confident, > buffer)",
          rev.verdict == "surplus" and rev.surplus > rev.buffer, f"surplus=${rev.surplus:,.0f}")

    salmon = results["CV2025115614"]
    check("Salmon: 8.625% rate parsed from June 1 2025",
          abs((salmon.interest_rate or 0) - 0.08625) < 1e-6 and salmon.interest_from_date == date(2025, 6, 1))

    canton = results["CV2025105047"]
    check("Canton: detected as tax decree by CONTENT (CV number, no CVG)",
          canton.is_tax_decree is True)

    passed = sum(_checks)
    total = len(_checks)
    print("\n" + "=" * 78)
    print(f"  RESULT: {passed}/{total} checks passed")
    print("=" * 78)
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
