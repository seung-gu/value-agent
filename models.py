"""Shared data models -- imported by the agents, the orchestrator, and the API.

Kept in a separate file to avoid circular imports.

Data shapes for the UI:
- SubIndustry (name + optional market_size) -> the sector's major sub-industries
- SubIndustry.companies     -> per-sub-industry market-share pie (who leads each market)
- CompanyPortfolio.portfolio-> per-company portfolio pie (how a company's revenue splits)
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Segment(BaseModel):
    """One slice of a company's business portfolio (portfolio pie)."""
    label: str          # e.g. "Cloud", "iPhone", "Services"
    percentage: float   # 0-100, source-backed


class CompanyShare(BaseModel):
    """One company's market share within a sub-industry (market-share pie)."""
    company: str
    share: float          # % within the sub-industry, source-backed
    evidence: str = ""    # short source-backed note (why/how this share); empty if none


class SubIndustry(BaseModel):
    """One major sub-industry of the sector (e.g. 'Cloud Infrastructure')."""
    name: str
    market_size: str = ""  # market size if a report gives it, else empty (no forced weight)
    companies: list[CompanyShare] = Field(default_factory=list)  # market-share pie
    sources: list[str] = Field(default_factory=list)


class CompanyPortfolio(BaseModel):
    """One company's business portfolio (portfolio pie). Output of company_agent."""
    name: str
    portfolio: list[Segment] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


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
