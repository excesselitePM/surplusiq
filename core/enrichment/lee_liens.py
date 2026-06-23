"""
Lee County — PR-FIRST lien-consumes-surplus validation.

Lee is the one county where the PropertyRadar lien check is the PRIMARY
validation, not the secondary docket enrichment the OH/FL docket counties use.
This module is the lien gate: it decides whether a junior lien (second mortgage /
HELOC / involuntary) consumes the apparent auction surplus, which would make the
lead invalid.

GROUNDED IN REAL PR DATA (Lee Investigate run 28053764053, 3 live Lee leads):
  • Itemized fields that EXIST on this PR plan (validated by auto-prune):
      TotalLoanBalance, NumberLoans, FirstAmount/FirstLoanType/FirstDate,
      SecondAmount/SecondLoanType/SecondDate, PropertyHasOpenLiens,
      PropertyHasOpenPersonLiens, isFreeAndClear, isListedForSale, LastTransferValue.
  • Fields that DO NOT exist on this plan (all 400): OpenLienCount, OpenLienBalance,
      InvoluntaryLienCount, InvoluntaryLienBalance, LienAmount, LienType, ThirdAmount.
  • The /properties/{RadarID}/{loans,liens,documents} SUB-RESOURCES all 404 — so the
      `GET /v1/documents/{DocumentID}` itemized-lien layer Eric asked about is NOT
      reachable on this plan. `SecondAmount` is the only itemized junior-lien amount
      available; involuntary/judgment/HUD liens are a BOOLEAN (PropertyHasOpenLiens)
      with NO amount.

THE OWNER-TIMING TRAP (proven on P7BF6453): PR returned the POST-auction owner
(UPSTATE ENTERPRISES LLC, isListedForSale=1) — its TotalLoanBalance ($30,378) is
the new investor's, NOT the foreclosed homeowner's ~$191K mortgage. Junior liens
that consume surplus belong to the FORMER owner. So:
  - use PROPERTY-keyed `PropertyHasOpenLiens`, never person-keyed
    `PropertyHasOpenPersonLiens` (that is the new owner's).
  - when PR shows a new owner (isListedForSale=1, or owner != docket owner), the
    lien amounts are UNTRUSTWORTHY → never HARD-KILL on them, and never certify a
    clean (no-lien) record as truly clean — the former owner's liens may be masked.

ANTI-FABRICATION: a hard kill requires a REAL itemized junior-lien amount
(SecondAmount) that exceeds the surplus AND trustworthy owner data. No extractable
amount → no kill. The coarse AVM−AvailableEquity / TotalLoanBalance path can only
raise a CAUTION (it is an estimate), never a kill. We never invent a lien figure.

This module is PURE (no network) so the acceptance test runs the exact production
logic against committed real PR records (data/samples/lee/ci/*_liens.json).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


# ── verdict ──────────────────────────────────────────────────────────────────
@dataclass
class LienVerdict:
    classification: str                 # killed | lien_risk | clean | pursuable
    is_hard_kill: bool                  # True only for a real itemized junior lien > surplus
    surplus: float
    lien_amount: float                  # the figure the verdict turned on (0 if none)
    lien_source: str                    # second_position | total_loan_balance | implied_avm_equity | none
    owner_timing_suspect: bool
    pr_owner: str = ""
    reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)   # lien_risk, owner_timing_suspect, liens_possibly_masked

    def summary(self) -> str:
        tag = self.classification.upper()
        amt = f"${self.lien_amount:,.0f}" if self.lien_amount else "—"
        return (f"{tag} (hard_kill={self.is_hard_kill}) lien={amt}/{self.lien_source} "
                f"surplus=${self.surplus:,.0f} owner_timing_suspect={self.owner_timing_suspect}")


# ── helpers ──────────────────────────────────────────────────────────────────
_COMPANY_RE = re.compile(
    r"\b(LLC|L\.L\.C|INC|CORP|CO|COMPANY|TRUST|LP|LLP|PLLC|ENTERPRISES|"
    r"HOLDINGS|PROPERTIES|INVESTMENTS|CAPITAL|GROUP|FUND|REALTY|HOMES|USA)\b",
    re.I,
)


def _money(v) -> float:
    """Coerce a PR numeric/str field to float; absent/blank → 0.0. Never raises."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def _truthy(v) -> bool:
    """PR returns 0/1 ints (sometimes strings) for boolean flags."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def _is_company(name: str) -> bool:
    return bool(name) and bool(_COMPANY_RE.search(name))


def _name_tokens(name: str) -> set:
    return set(re.sub(r"[^A-Za-z ]", " ", (name or "").upper()).split())


def _names_overlap(a: str, b: str) -> bool:
    """Do two owner strings share a meaningful surname token? Tolerates
    'LAST, FIRST' vs 'FIRST LAST' and company noise."""
    ta = _name_tokens(a) - _COMPANY_STOP
    tb = _name_tokens(b) - _COMPANY_STOP
    return bool(ta & tb)


# tokens that don't prove identity overlap
_COMPANY_STOP = {"LLC", "INC", "CORP", "CO", "COMPANY", "TRUST", "LP", "LLP",
                 "USA", "THE", "OF", "AND", "ENTERPRISES", "HOLDINGS",
                 "PROPERTIES", "GROUP", "REALTY", "HOMES"}


# ── owner-timing trap ────────────────────────────────────────────────────────
def detect_owner_timing(pr_record: dict, docket_owner: Optional[str] = None):
    """Return (suspect: bool, reasons: list[str]). PR enrichment on a freshly sold
    foreclosure can return the NEW (post-auction) owner; their lien profile says
    nothing about whether the FORMER owner's junior liens consumed the surplus."""
    reasons = []
    owner = str(pr_record.get("Owner") or "")
    if _truthy(pr_record.get("isListedForSale")):
        reasons.append("isListedForSale=1 — relisted post-auction, PR owner is almost "
                       "certainly the NEW owner")
    if docket_owner and owner and not _names_overlap(owner, docket_owner):
        reasons.append(f"PR owner '{owner}' shares no surname with docket owner "
                       f"'{docket_owner}' — ownership changed since the judgment")
    suspect = bool(reasons)
    # A company owner alone isn't proof (could be a foreclosed LLC-held rental), but
    # it corroborates a relisting/mismatch signal.
    if suspect and _is_company(owner):
        reasons.append(f"PR owner '{owner}' is a company/LLC — consistent with an "
                       f"investor flip")
    return suspect, reasons


# ── primary classifier ───────────────────────────────────────────────────────
def classify_lien_surplus(pr_record: dict, surplus: float,
                          docket_owner: Optional[str] = None) -> LienVerdict:
    """PR-first lien-consumes-surplus decision for one Lee lead.

    Precedence (most severe first):
      1. HARD KILL  — itemized SecondAmount > surplus AND owner-timing trustworthy.
      2. CAUTION    — lien present but the amount is an estimate or owner-timing
                      makes it untrustworthy (never a kill).
      3. CLEAN      — no open lien AND owner-timing trustworthy.
      else PURSUABLE — lien present but does not consume the surplus.
    """
    surplus = _money(surplus)
    owner = str(pr_record.get("Owner") or "")
    has_liens = _truthy(pr_record.get("PropertyHasOpenLiens"))     # PROPERTY-keyed only
    second_amt = _money(pr_record.get("SecondAmount"))
    total_lb = _money(pr_record.get("TotalLoanBalance"))
    avm = _money(pr_record.get("AVM"))
    avail_eq = _money(pr_record.get("AvailableEquity"))

    suspect, ot_reasons = detect_owner_timing(pr_record, docket_owner)
    reasons = list(ot_reasons)
    flags = []
    if suspect:
        flags.append("owner_timing_suspect")

    # implied lien (estimate): prefer the stated aggregate balance, else AVM−equity.
    implied = total_lb if total_lb > 0 else max(0.0, avm - avail_eq)

    def verdict(classification, hard_kill, amount, source):
        return LienVerdict(
            classification=classification, is_hard_kill=hard_kill, surplus=surplus,
            lien_amount=amount, lien_source=source, owner_timing_suspect=suspect,
            pr_owner=owner, reasons=reasons, flags=flags,
        )

    # 1 ── HARD KILL: real itemized junior-lien amount exceeds the surplus, and the
    #      PR owner data is trustworthy (not a post-auction flip). Anti-fabrication
    #      is satisfied because SecondAmount is a real, itemized figure.
    if second_amt > 0 and second_amt > surplus:
        if not suspect:
            reasons.append(f"itemized second-position lien ${second_amt:,.0f} exceeds "
                           f"surplus ${surplus:,.0f} — surplus consumed")
            return verdict("killed", True, second_amt, "second_position")
        # lien exceeds surplus BUT owner-timing suspect → cannot confirm it's the
        # FORMER owner's lien; downgrade to caution, never kill on untrusted data.
        flags.append("lien_risk")
        reasons.append(f"second-position lien ${second_amt:,.0f} exceeds surplus "
                       f"${surplus:,.0f}, but owner-timing is suspect — cannot confirm "
                       f"the lien belongs to the foreclosed owner; NOT killing")
        return verdict("lien_risk", False, second_amt, "second_position")

    # 2 ── CAUTION paths (estimate or untrusted; never a kill)
    if has_liens and suspect:
        # Lien present, but the amount on file is the new owner's → untrustworthy.
        flags.append("lien_risk")
        reasons.append("open lien present but owner-timing suspect — the lien amount "
                       "on file is the new owner's, not the foreclosed owner's; "
                       "treat as caution, not clean")
        amt = implied
        return verdict("lien_risk", False, amt,
                       "total_loan_balance" if total_lb > 0 else "implied_avm_equity")

    if has_liens and not suspect and implied >= surplus and implied > 0:
        # Coarse estimate consumes the surplus — caution, not kill (no itemized amount).
        flags.append("lien_risk")
        reasons.append(f"no itemized junior amount, but estimated lien ${implied:,.0f} "
                       f"(AVM−equity / TotalLoanBalance) ≥ surplus ${surplus:,.0f} — "
                       f"caution (estimate, not a confirmed kill)")
        return verdict("lien_risk", False, implied,
                       "total_loan_balance" if total_lb > 0 else "implied_avm_equity")

    # 3 ── CLEAN / masked
    if not has_liens:
        if suspect:
            # No lien on the NEW owner's record — but the former owner's liens may be
            # masked. Don't certify clean; flag it.
            flags.append("liens_possibly_masked")
            reasons.append("no open lien on PR's record, but owner-timing suspect — "
                           "the foreclosed owner's liens may be masked by the new "
                           "owner's clean profile; not certifying clean")
            return verdict("clean", False, 0.0, "none")
        reasons.append("no open property lien and owner-timing trustworthy — clean surplus")
        return verdict("clean", False, 0.0, "none")

    # else ── lien present, amount (where known) does not consume the surplus.
    reasons.append(f"open lien present but does not consume surplus "
                   f"(estimated lien ${implied:,.0f} < surplus ${surplus:,.0f})")
    return verdict("pursuable", False, implied,
                   "total_loan_balance" if total_lb > 0 else
                   ("implied_avm_equity" if implied > 0 else "none"))


# ── production field list (under PR's 50-field cap; 'Card,<extra>' 400s) ──────
# The targeted Fields the Lee enrichment GET must request to feed this classifier.
LEE_LIEN_FIELDS = [
    "RadarID", "Owner", "AVM", "AssessedValue", "AvailableEquity",
    "TotalLoanBalance", "NumberLoans",
    "FirstAmount", "FirstLoanType", "FirstDate",
    "SecondAmount", "SecondLoanType", "SecondDate",
    "PropertyHasOpenLiens", "PropertyHasOpenPersonLiens",
    "isFreeAndClear", "isListedForSale", "LastTransferValue",
]


# ── docket layer stub (delayed-docket scheduler is parked, do NOT build here) ─
def docket_rescan_hook(lead: dict) -> None:
    """Placeholder so the future system-wide delayed-docket reverification model
    (3/5/10/21-day cadence) can attach a Lee docket pass later. Intentionally a
    no-op: per scope, the Lee-only scheduler is NOT built here (it would duplicate
    the parked system-wide rescan model). The Lee portal (matrix.leeclerk.org) is
    additionally walled headless from CI — ERR_HTTP2_PROTOCOL_ERROR even from a
    real browser on a datacenter IP (Lee Investigate run 28053235111) — so any
    future docket layer needs a non-datacenter path first. See FINDINGS.md."""
    return None
