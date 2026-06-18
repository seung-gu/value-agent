"""Shared data models -- imported by several modules (sector_agent, judge, eval).

Kept in a separate file to avoid circular imports
(sector_agent and judge both import SectorAnalysis from here).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompetitorCompany(BaseModel):
    name: str
    reason: str  # why it is competitive in this sector
    # Quantitative backing from sources: market share %, revenue, growth rate, capex, etc.
    # If no figure is available from sources, state that explicitly -- never invent numbers.
    evidence: str


class SectorAnalysis(BaseModel):
    sector: str                       # GICS sector name
    market_size: str                  # value + year (e.g. "$1.77B (2026)")
    cagr: str                         # % + period (e.g. "23.8% (2026-2032)")
    potential_score: float = Field(ge=0, le=100)  # score for ranking/comparing sectors
    top_companies: list[CompetitorCompany]        # discovered competitive companies
    key_drivers: list[str] = Field(default_factory=list)        # growth drivers
    extra_metrics: dict[str, str] = Field(default_factory=dict)  # extra metrics the agent gathered itself
    sources: list[str] = Field(default_factory=list)            # source URLs (anti-hallucination)
    confidence: float = Field(ge=0, le=1, default=0.5)
