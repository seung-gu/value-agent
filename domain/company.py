"""Company domain -- the static company master + its quarterly revenue breakdown.

`Company` is the static master (name, url), identified by a surrogate code so it is stored
once and referenced (not duplicated) by every market_share / portfolio row. `CompanyPortfolio`
is one revenue segment for one period (time-series) -- the portfolio pie, accumulated per period.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Company(BaseModel):
    """A company -- static master, referenced by surrogate code."""

    company_code: str   # surrogate, e.g. "C001"
    name: str
    url: str = ""


class CompanyPortfolio(BaseModel):
    """One revenue segment of a company for one period (time-series row of the portfolio pie)."""

    company_code: str                        # FK -> Company.company_code
    period: str                              # "2026-Q2"
    segment: str                             # "Cloud", "iPhone", ...
    percentage: float = Field(ge=0, le=100)  # % of company revenue
    source: str = ""
