"""Company domain -- a company's business portfolio (the portfolio pie)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Segment(BaseModel):
    """One slice of a company's business portfolio."""

    label: str          # e.g. "Cloud", "iPhone", "Services"
    percentage: float   # 0-100, source-backed


class CompanyPortfolio(BaseModel):
    """One company's business portfolio (portfolio pie). Output of company_agent."""

    name: str
    portfolio: list[Segment] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
