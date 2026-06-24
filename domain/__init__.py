"""Domain entities -- pure data, no dependencies on ports/adapters.

Seven entities mirroring the DB tables: three STATIC (GicsReference, SubIndustry, Company)
and four TIME-SERIES (SubIndustryKpi, MarketShare, CompanyPortfolio, CompanyFinancials).
Re-exported so callers can `from domain import MarketShare` regardless of the module it lives in.
"""

from domain.company import Company, CompanyFinancials, CompanyPortfolio
from domain.gics_reference import GicsReference
from domain.market_share import MarketShare
from domain.sub_industry import SubIndustry, SubIndustryKpi

__all__ = [
    # static
    "GicsReference",
    "SubIndustry",
    "Company",
    # time-series
    "SubIndustryKpi",
    "MarketShare",
    "CompanyPortfolio",
    "CompanyFinancials",
]
