"""
Cuyahoga OH-mortgage conservative debt extractor — ACCEPTANCE TEST.

Runs the production logic (core/dockets/oh_debt.parse_cuyahoga_mortgage_debt →
compute_debt) against 5 REAL captured Cuyahoga decrees
(data/samples/cuyahoga/ci/*_judgment.txt) with their real sale dates + prices.

The headline: CV19923457 is a ~$100K LIVE false-positive (dashboard shows +$48,520;
conservative debt makes it −$53,124) — it MUST flip to killed. Split-balance is
critical: interest accrues on the interest-bearing $233,037, NOT the full $334,080.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.dockets.oh_debt import parse_cuyahoga_mortgage_debt, compute_debt, DEFAULT_BUFFER_PCT

SAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "data", "samples", "cuyahoga", "ci"))
_checks = []


def check(name, cond, detail=""):
    _checks.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def run(case, sale_date, sale_price):
    txt = open(os.path.join(SAMPLES, f"{case}_judgment.txt"), encoding="utf-8").read()
    return compute_debt(parse_cuyahoga_mortgage_debt(txt), sale_date, sale_price)


# (case, sale_date, sale_price, expected_verdict, label)
CASES = [
    ("CV19923457", date(2026, 6, 22), 382600.0, "killed",  "CV19923457 ($100K live false-positive)"),
    ("CV25115432", date(2026, 6, 15), 161920.0, "surplus", "CV25115432 (genuine surplus)"),
    ("CV25110566", date(2026, 6, 15),  41700.0, "surplus", "CV25110566 ('per year' rate)"),
    ("CV25111605", date(2026, 6, 22), 103100.0, "killed",  "CV25111605 (multi-period rate)"),
    ("CV24106082", date(2026, 6, 22), 163100.0, "killed",  "CV24106082 (deferred split-balance)"),
    ("CV24108223", date(2026, 6, 22), 103100.0, "killed",  "CV24108223 (itemized total/interest-bearing/deferred split)"),
    ("CV24106266", date(2026, 6, 15), 109200.0, "killed",  "CV24106266 ('GRANTED a judgment' + 'plus regular interest')"),
    ("CV25117671", date(2026, 6, 15), 132300.0, "killed",  "CV25117671 ('Judgment in Foreclosure is rendered')"),
]


def main():
    print("=" * 80)
    print(f"  Cuyahoga OH-mortgage conservative debt — acceptance "
          f"(buffer {DEFAULT_BUFFER_PCT*100:.0f}% of principal)")
    print("=" * 80)

    res = {}
    for case, sd, sp, expected, label in CASES:
        d = run(case, sd, sp)
        res[case] = d
        print(f"\n{label}  ({case})  sale ${sp:,.0f} on {sd}")
        print(f"   principal=${d.principal:,.2f}  base=${d.interest_base:,.2f}  rate="
              f"{(str(round(d.interest_rate*100,3))+'%') if d.interest_rate else 'none'}"
              f"  from={d.interest_from_date}")
        print(f"   + interest=${d.accrued_interest:,.0f}  + junior=${d.junior_liens:,.0f}"
              f"  + buffer=${d.buffer:,.0f}  = DEBT ${d.total_debt:,.0f}")
        print(f"   surplus=${d.surplus:,.0f}  → {d.verdict.upper()}" + (f"  [{d.flag}]" if d.flag else ""))
        check(f"{label}: verdict == {expected}", d.verdict == expected, d.verdict)
        check(f"{case}: principal anchored (no $0)", d.principal > 0, f"${d.principal:,.0f}")

    # Headline + split-balance correctness.
    a = res["CV19923457"]
    check("CV19923457: SPLIT-BALANCE base is $233,037 (interest-bearing), NOT full $334,080",
          abs(a.interest_base - 233037.47) < 1 and abs(a.principal - 334079.82) < 1,
          f"base=${a.interest_base:,.0f} total=${a.principal:,.0f}")
    check("CV19923457: flips the live false-positive to NO surplus (was +$48,520)",
          a.surplus is not None and a.surplus < 0, f"surplus=${a.surplus:,.0f}")

    check("CV25110566: 'per year' rate parsed (6.25%)",
          abs((res["CV25110566"].interest_rate or 0) - 0.0625) < 1e-6)
    check("CV25111605: multi-period → earliest 23% from Aug 2024 (conservative)",
          abs((res["CV25111605"].interest_rate or 0) - 0.23) < 1e-6
          and res["CV25111605"].interest_from_date == date(2024, 8, 1))
    check("CV24106082: deferred split-balance ($95,595.98 non-interest-bearing)",
          abs(res["CV24106082"].principal - res["CV24106082"].interest_base - 95595.98) < 1)
    # CV24108223: itemized "total $105,920.06 = interest-bearing $81,664.90 +
    # non-interest-bearing deferred $24,255.16". Base must be the $81,664.90
    # (NOT the $105,920.06 total, NOT the $24,255.16 deferred), and the explicit
    # interest-bearing anchor must NOT have grabbed a "non-interest-bearing" figure.
    c = res["CV24108223"]
    check("CV24108223: itemized split — base=$81,664.90 (interest-bearing only)",
          abs(c.interest_base - 81664.90) < 1, f"base=${c.interest_base:,.2f}")
    check("CV24108223: total principal = $105,920.06 (interest-bearing + deferred)",
          abs(c.principal - 105920.06) < 1, f"total=${c.principal:,.2f}")
    check("CV24108223: 2.875% rate parsed (interest computable)",
          abs((c.interest_rate or 0) - 0.02875) < 1e-6, f"rate={c.interest_rate}")
    # CV24106266: "Plaintiff is GRANTED a judgment against … in the sum of
    # $129,871.15 plus regular interest" — generic anchor must grab $129,871.15
    # despite the non-standard lead-in verb. Principal alone kills it.
    check("CV24106266: 'sum of $X plus regular interest' principal = $129,871.15",
          abs(res["CV24106266"].interest_base - 129871.15) < 1,
          f"base=${res['CV24106266'].interest_base:,.2f}")
    # CV25117671: "Judgment IN FORECLOSURE is rendered …" — single clean 2.625% rate.
    check("CV25117671: principal $116,793.75 + 2.625% rate parsed",
          abs(res["CV25117671"].interest_base - 116793.75) < 1
          and abs((res["CV25117671"].interest_rate or 0) - 0.02625) < 1e-6,
          f"base=${res['CV25117671'].interest_base:,.2f} rate={res['CV25117671'].interest_rate}")
    check("CV25115432: genuine surplus survives (> buffer, confident)",
          res["CV25115432"].verdict == "surplus" and res["CV25115432"].surplus > res["CV25115432"].buffer)

    passed, total = sum(_checks), len(_checks)
    print("\n" + "=" * 80)
    print(f"  RESULT: {passed}/{total} checks passed")
    print("=" * 80)
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
