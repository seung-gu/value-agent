"""Domain entities -- pure data, no dependencies on ports/adapters.

Re-exported so callers can `from domain import SubIndustry` regardless of which module
(sector/sub_industry/company) an entity lives in.
"""

from domain.company import CompanyPortfolio, Segment
from domain.sector import SectorAnalysis
from domain.sub_industry import CompanyShare, SubIndustry

__all__ = [
    "Segment",
    "CompanyPortfolio",
    "CompanyShare",
    "SubIndustry",
    "SectorAnalysis",
]
