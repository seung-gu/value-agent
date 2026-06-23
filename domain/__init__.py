"""Domain entities -- pure data, no dependencies on ports/adapters.

Six entities mirroring the DB tables: three STATIC (GicsReference, SubIndustry, Company)
and three TIME-SERIES (SubIndustryMetric, MarketShare, CompanyPortfolio). Re-exported so
callers can `from domain import MarketShare` regardless of the module it lives in.
"""

from domain.company import Company, CompanyPortfolio
from domain.gics_reference import GicsReference
from domain.market_share import MarketShare
from domain.sub_industry import SubIndustry, SubIndustryMetric

__all__ = [
    # static
    "GicsReference",
    "SubIndustry",
    "Company",
    # time-series
    "SubIndustryMetric",
    "MarketShare",
    "CompanyPortfolio",
]
