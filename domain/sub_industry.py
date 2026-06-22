"""Sub-industry domain -- a sub-industry and its companies' market shares."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompanyShare(BaseModel):
    """One company's market share within a sub-industry (market-share pie)."""

    company: str
    share: float          # % within the sub-industry, source-backed
    evidence: str = ""    # short source-backed note; empty if none


class SubIndustry(BaseModel):
    """One major sub-industry of a sector (e.g. 'Cloud Infrastructure')."""

    name: str
    market_size: str = ""  # market size if a report gives it, else empty
    companies: list[CompanyShare] = Field(default_factory=list)  # market-share pie
    sources: list[str] = Field(default_factory=list)
