"""Market-share domain -- a company's share within a sub-industry (time-series).

The N:M junction between sub_industry and company, plus `period`: one row = one company's
share of one sub-industry in one quarter. Same shape as company_portfolio (a breakdown that
sums to ~100% per period), accumulated one row per period.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MarketShare(BaseModel):
    """One company's market share within a sub-industry for one period (time-series row)."""

    sub_code: str                            # FK -> SubIndustry.sub_code
    company_code: str                        # FK -> Company.company_code
    period: str                              # "2026-Q2"
    percentage: float = Field(ge=0, le=100)  # % within the sub-industry
    source: str = ""
