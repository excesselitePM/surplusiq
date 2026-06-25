"""
SurplusIQ — OH MORTGAGE foreclosure debt extractor (conservative).

Replaces the single-figure `max()`-near-keyword approach
(`montgomery.py:extract_debt_from_pdf_bytes`) which ships FALSE-POSITIVE
surpluses: it grabs the largest dollar near any judgment keyword (often the
original loan amount or a legal-description figure) and ignores accruing
interest, junior liens, and costs. Proven failure — 6973 Van Buren
(CV2024125264): reads $244,898.61 principal → "+$32K surplus" vs $277,300 sale,
but real debt (principal + interest + costs) erases it.

Built against 5 REAL Summit judgment PDFs (data/samples/summit/ci/*_judgment.txt).
Conservative OH-mortgage debt =
    principal
  + accrued interest (rate × interest-bearing base × years from_date→sale_date)
  + stated junior-lien payouts
  + a buffer for the never-quantified late charges / advances / costs
A false-positive surplus is worse than a missed marginal lead, so the buffer and
the confidence flags push borderline cases (debt near sale) to the safe side.

PURE / network-free → the acceptance test runs the exact production logic against
the committed real judgment text.

TAX vs MORTGAGE: tax decrees (RC Chapter 5721, Minimum Bid) are a different shape
— this module DETECTS them by content (is_oh_tax_decree) and refuses to apply
mortgage logic. NOTE: the case-number `is_oh_tax_case()` ('CVG') does NOT catch
tax cases filed under a plain 'CV' number (e.g. Canton CV2025105047), so this
content check is the real safety net.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# Buffer for the never-quantified components every OH foreclosure decree lists as
# accruing-but-unstated: "all late charges, all advances [property taxes, hazard
# insurance, property preservation], all costs and expenses ... ascertained at the
# time of sale." 10% of principal — justification: OH property-tax advances run
# ~1.5–2.5%/yr of value and accrue over a 1–3 yr foreclosure pendency (~3–7% of
# principal), plus insurance/preservation, statutory attorney fees, and court
# costs (a few thousand). 10% is a central conservative estimate that SCALES with
# loan size, calibrated to push the proven false-positive (6973) safely to
# no-surplus while preserving genuine surpluses (Revere keeps $105K). Single
# tunable constant — raise it to be more conservative.
DEFAULT_BUFFER_PCT = 0.10

_MONEY = r"\$\s*([\d,]+\.\d{2})"


def _money(s: str) -> float:
    return float(s.replace(",", "").replace("$", "").strip())


def _norm(text: str) -> str:
    """Collapse whitespace/newlines so cross-line sentences match as one string."""
    return re.sub(r"\s+", " ", text or "")


def is_oh_tax_decree(full_text: str) -> bool:
    """Content-based tax-decree detector (RC 5721). Mortgage debt logic must NOT
    run on these. Catches tax cases the case-number 'CVG' check misses."""
    # Tax-UNIQUE markers only. NB: "minimum bid" and "fiscal officer" also appear in
    # MORTGAGE decrees (the §2329.20 second-auction provision; the fiscal officer's
    # tax-payment priority) — verified across the 5 real decrees — so they are NOT
    # usable. RC 5721 / "delinquent land tax" / "delinquent real estate" are.
    low = (full_text or "").lower()
    return any(m in low for m in (
        "chapter 5721", "5721 of the ohio", "delinquent land tax", "delinquent real estate",
    ))


def _parse_decree_date(s: str) -> Optional[date]:
    s = s.replace(",", "").strip()
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class OHMortgageDebt:
    is_tax_decree: bool = False
    principal: float = 0.0
    interest_rate: Optional[float] = None        # decimal, e.g. 0.04075
    interest_from_date: Optional[date] = None
    interest_base: float = 0.0                    # principal, or "accruing on the sum of $X"
    junior_liens: float = 0.0
    accrued_interest: float = 0.0                 # computed to sale_date
    buffer: float = 0.0
    total_debt: float = 0.0
    has_computable_interest: bool = False
    surplus: Optional[float] = None               # sale_price − total_debt
    verdict: str = "unknown"                      # killed | surplus | flag_uncertain | tax_decree | unknown
    flag: str = ""                                # "" or a manual-review reason
    notes: list = field(default_factory=list)


def parse_oh_mortgage_debt(full_text: str) -> OHMortgageDebt:
    """Parse the structured debt components from a judgment-decree text.
    Network-free; pairs with compute_debt() (which needs the sale date)."""
    r = OHMortgageDebt()
    if is_oh_tax_decree(full_text):
        r.is_tax_decree = True
        r.verdict = "tax_decree"
        r.notes.append("RC 5721 / Minimum Bid tax decree — mortgage logic skipped")
        return r

    norm = _norm(full_text)

    # 1) PRINCIPAL — anchor on the real sentence, NOT max()-near-keyword. The first
    #    dollar figure after "due [to/the] Plaintiff ... on the [promissory] Note".
    # Tolerant of the real phrasing variants seen across decrees:
    #   "due Plaintiff on the promissory note $X"
    #   "due to Plaintiff on the Note principal in the amount of $X"
    #   "due the Plaintiff, on the promissory note ... the sum of $X"
    #   "due and owing to the Plaintiff, on the Note, the principal balance of $X"
    # [^$] stops at the FIRST dollar figure after "on the Note" = the principal.
    m = re.search(
        r"due\b[^$]{0,60}?Plaintiff\b[^$]{0,160}?on\s+the\s+(?:promissory\s+)?Note\b"
        r"[^$]{0,160}?" + _MONEY,
        norm, re.I,
    )
    if not m:
        r.verdict = "unknown"
        r.notes.append("principal not found (no 'due Plaintiff on the Note $X' anchor)")
        return r
    r.principal = _money(m.group(1))
    principal_end = m.end()
    r.interest_base = r.principal  # default; overridden by "accruing on the sum of"

    # 2) INTEREST RATE + FROM-DATE — must be the PRINCIPAL's, so search only the
    #    window right after the principal (avoids grabbing a JUNIOR lien's rate,
    #    e.g. Royal County Down's condo 8% which sits far later in the decree).
    window = norm[principal_end:principal_end + 300]
    # Accept both "4.075%" and "2.99000 percent" — decrees use either.
    m_rate = re.search(
        r"([\d.]+)\s*(?:%|percent)\s*per\s+annum\s+from\s+([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})",
        window, re.I,
    )
    if m_rate:
        try:
            r.interest_rate = float(m_rate.group(1)) / 100.0
        except ValueError:
            r.interest_rate = None
        r.interest_from_date = _parse_decree_date(m_rate.group(2))
        r.has_computable_interest = bool(r.interest_rate and r.interest_from_date)

    # 2b) INTEREST-BEARING BASE — "accruing on the sum of $X" means interest runs on
    #     a SUBSET of principal (the rest is a deferred non-interest-bearing
    #     balance). 6973: interest accrues on $138,242.97, NOT the full $244,898.61.
    #     Missing this OVER-estimates debt on split-balance loans.
    m_base = re.search(r"accruing\s+on\s+the\s+sum\s+of\s+" + _MONEY,
                       norm[principal_end:principal_end + 400], re.I)
    if m_base:
        r.interest_base = _money(m_base.group(1))
        r.notes.append(f"split-balance: interest accrues on ${r.interest_base:,.2f}, "
                       f"not full principal ${r.principal:,.2f}")

    # 3) JUNIOR LIENS — stated junior-party payouts ("for a total amount of $X"),
    #    e.g. Royal County Down's $13,028.40 condo judgment. The old extractor
    #    ignored these entirely. Exclude any figure equal to the principal.
    for jm in re.finditer(r"for\s+a\s+total\s+amount\s+of\s+" + _MONEY, norm, re.I):
        amt = _money(jm.group(1))
        if abs(amt - r.principal) > 1.0:
            r.junior_liens += amt
    if r.junior_liens:
        r.notes.append(f"junior lien(s) added: ${r.junior_liens:,.2f}")

    if not r.has_computable_interest:
        r.notes.append("no computable interest rate/date ('with interest' only — "
                       "old decree); accrued interest = 0, debt may be understated")
    return r


def compute_debt(parsed: OHMortgageDebt, sale_date: date, sale_price: float,
                 buffer_pct: float = DEFAULT_BUFFER_PCT) -> OHMortgageDebt:
    """Compute accrued interest to sale_date, total conservative debt, and the
    verdict. Mutates and returns `parsed`."""
    if parsed.is_tax_decree or parsed.principal <= 0:
        return parsed  # tax decree, or principal not parseable → nothing to compute

    # Accrued interest = rate × interest-bearing base × years(from_date → sale_date).
    if parsed.has_computable_interest and sale_date and parsed.interest_from_date:
        years = max(0.0, (sale_date - parsed.interest_from_date).days / 365.25)
        parsed.accrued_interest = parsed.interest_rate * parsed.interest_base * years
        parsed.notes.append(
            f"interest = {parsed.interest_rate*100:.3f}% × ${parsed.interest_base:,.0f} "
            f"× {years:.2f}yr = ${parsed.accrued_interest:,.0f}")

    parsed.buffer = round(buffer_pct * parsed.principal, 2)
    parsed.total_debt = round(
        parsed.principal + parsed.accrued_interest + parsed.junior_liens + parsed.buffer, 2)
    parsed.surplus = round(sale_price - parsed.total_debt, 2)

    # VERDICT — false-positive-averse precedence.
    if parsed.surplus <= 0:
        parsed.verdict = "killed"
        parsed.flag = ""
        parsed.notes.append(f"conservative debt ${parsed.total_debt:,.0f} ≥ sale "
                            f"${sale_price:,.0f} — NO surplus")
    elif not parsed.has_computable_interest:
        parsed.verdict = "flag_uncertain"
        parsed.flag = "surplus uncertain — interest not computable (old decree); manual verify"
    elif parsed.surplus < parsed.buffer:
        parsed.verdict = "flag_uncertain"
        parsed.flag = (f"surplus ${parsed.surplus:,.0f} within buffer "
                       f"${parsed.buffer:,.0f} — marginal, manual review")
    else:
        parsed.verdict = "surplus"
        parsed.flag = ""
    return parsed


def oh_mortgage_debt(full_text: str, sale_date: date, sale_price: float,
                     buffer_pct: float = DEFAULT_BUFFER_PCT) -> OHMortgageDebt:
    """Convenience: parse + compute in one call."""
    return compute_debt(parse_oh_mortgage_debt(full_text), sale_date, sale_price, buffer_pct)


# ── component storage (Option a): scraper parses & stores; enrich computes ──
def components_dict(d: OHMortgageDebt) -> dict:
    """Serialize the parse-time inputs the sale-date computation needs. Stored on
    the DocketResult (and the saved jsonl) so the debt breakdown is auditable."""
    return {
        "is_tax_decree": d.is_tax_decree,
        "principal": d.principal,
        "interest_rate": d.interest_rate,
        "interest_from_date": d.interest_from_date.isoformat() if d.interest_from_date else None,
        "interest_base": d.interest_base,
        "junior_liens": d.junior_liens,
        "has_computable_interest": d.has_computable_interest,
        "notes": list(d.notes),
    }


def compute_from_components(comp: dict, sale_date: date, sale_price: float,
                           buffer_pct: float = DEFAULT_BUFFER_PCT) -> OHMortgageDebt:
    """Rebuild an OHMortgageDebt from a stored components dict and run the
    sale-date computation. Used by enrich.run_county where the sale date lives."""
    fd = comp.get("interest_from_date")
    d = OHMortgageDebt(
        is_tax_decree=bool(comp.get("is_tax_decree")),
        principal=float(comp.get("principal") or 0.0),
        interest_rate=comp.get("interest_rate"),
        interest_from_date=(date.fromisoformat(fd) if fd else None),
        interest_base=float(comp.get("interest_base") or 0.0),
        junior_liens=float(comp.get("junior_liens") or 0.0),
        has_computable_interest=bool(comp.get("has_computable_interest")),
        notes=list(comp.get("notes") or []),
    )
    return compute_debt(d, sale_date, sale_price, buffer_pct)
