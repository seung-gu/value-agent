"""Sector domain -- the top-level sector analysis (contains sub-industries + companies)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from domain.company import CompanyPortfolio
from domain.sub_industry import SubIndustry


class SectorAnalysis(BaseModel):
    """Final merged result for one sector."""

    sector: str
    market_size: str
    cagr: str
    potential_score: float = Field(ge=0, le=100)
    sub_industries: list[SubIndustry] = Field(default_factory=list)          # ~5 sub-industries
    company_portfolios: list[CompanyPortfolio] = Field(default_factory=list)  # top companies
    key_drivers: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, default=0.5)
