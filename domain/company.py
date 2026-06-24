"""Company domain -- the static company master + its time-series facts.

`Company` is the static master (name, ticker), identified by a surrogate code so it is stored
once and referenced (not duplicated) by every market_share / portfolio / financials row.
`CompanyPortfolio` is one revenue stream for one period (segment breakdown that sums to total
revenue); `CompanyFinancials` is one independent financial metric for one period. Both are
time-series, accumulated one row per period.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Company(BaseModel):
    """A company -- static master, referenced by surrogate code."""

    company_code: str           # surrogate, e.g. "C0001"
    name: str
    ticker: str | None = None   # UNIQUE when present; absent for unlisted/foreign companies


class CompanyPortfolio(BaseModel):
    """One revenue stream of a company for a period (segment breakdown; streams sum to revenue)."""

    company_code: str            # FK -> Company.company_code
    period: str                  # "FY2025"
    stream: str                  # "iPhone", "Services", ...
    amount: float = Field(ge=0)  # USD revenue attributed to the stream
    source: str = ""


class CompanyFinancials(BaseModel):
    """One financial metric of a company for a period (independent figure, USD)."""

    company_code: str   # FK -> Company.company_code
    period: str         # "FY2025"
    account: str        # "revenue", "operating_income", "net_income", "operating_cash_flow", ...
    amount: float       # USD (may be negative -- e.g. a net loss)
    source: str = ""
