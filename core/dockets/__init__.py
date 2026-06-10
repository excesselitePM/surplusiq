"""
SurplusIQ — Docket Scraper Module

Each county has its own docket scraper subclass.
Use `enrich_lead()` to attach docket data to a Lead record.

CLI usage:
  python -m core.dockets cuyahoga-oh CV25110711
  python -m core.dockets miami-dade-fl 2017-021344-CA-01
  python -m core.dockets cuyahoga-oh                 # runs against all current Cuyahoga leads
"""

from .base import DocketScraper, DocketResult, DocketEvent
from .cuyahoga import CuyahogaDocketScraper, parse_cuyahoga_case_number
from .miami_dade import MiamiDadeDocketScraper, parse_miami_dade_case_number
from .franklin import FranklinDocketScraper, parse_franklin_case_number
from .montgomery import MontgomeryDocketScraper, parse_montgomery_case_number
from .summit import SummitDocketScraper, parse_summit_case_number
from .hamilton import HamiltonDocketScraper
from .broward import BrowardDocketScraper, parse_broward_case_number


# Registry — add new counties here as they're implemented
SCRAPER_REGISTRY = {
    "cuyahoga-oh":   CuyahogaDocketScraper,
    "miami-dade-fl": MiamiDadeDocketScraper,
    "franklin-oh":   FranklinDocketScraper,
    "montgomery-oh": MontgomeryDocketScraper,
    "summit-oh":     SummitDocketScraper,
    "hamilton-oh":   HamiltonDocketScraper,
    "broward-fl":    BrowardDocketScraper,
}


def get_scraper(county_id: str, headless: bool = True) -> DocketScraper:
    cls = SCRAPER_REGISTRY.get(county_id)
    if not cls:
        raise NotImplementedError(f"No docket scraper for {county_id} yet")
    return cls(headless=headless)


__all__ = [
    "DocketScraper",
    "DocketResult",
    "DocketEvent",
    "CuyahogaDocketScraper",
    "MiamiDadeDocketScraper",
    "FranklinDocketScraper",
    "MontgomeryDocketScraper",
    "SummitDocketScraper",
    "HamiltonDocketScraper",
    "BrowardDocketScraper",
    "get_scraper",
    "SCRAPER_REGISTRY",
]
