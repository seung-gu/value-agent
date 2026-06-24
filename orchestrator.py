"""Orchestrator (application layer) -- wires agents + repositories into the analysis flow.

Two flows:
- TAXONOMY (define sub-industries): `propose_taxonomy` runs sub_industry_agent (ReAct) for an
  industry group; `save_taxonomy` persists the approved list with surrogate sub_codes. The
  human-in-the-loop refine happens at the API layer (propose -> refine -> save).
- ANALYZE (fill market shares): for a sector, read its industry groups -> their (already
  defined) sub-industries -> fan out market_share_agent per sub-industry -> upsert the
  companies (surrogate codes) + store market_share rows for the period.

Depends on the repository PORTS, never on adapters. A stored row's `period` is the DATA's own
reporting date (the agent's `as_of`), and cache freshness is judged from it (see `is_fresh`),
NOT from when we happened to fetch it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from agents.company_agent import research_company
from agents.market_share_agent import research_market_share
from agents.sub_industry_agent import (
    SubIndustryFinding,
    SubIndustryProposal,
    propose_sub_industries,
)
from domain import (
    Company,
    CompanyFinancials,
    CompanyPortfolio,
    GicsReference,
    MarketShare,
    SubIndustry,
)
from ports.repository import StaticRepository, TimeSeriesRepository
from ports.search_client import SearchClient


def current_period() -> str:
    """Fallback period for when the agent couldn't read a reporting date, e.g. '2026-Q2'."""
    now = datetime.now(timezone.utc)
    return f"{now.year}-Q{(now.month - 1) // 3 + 1}"


def is_fresh(period: str) -> bool:
    """A stored reporting period (e.g. '2024', '2024-Q4', 'FY2025') is fresh enough to reuse
    if its year is within the last two (this year, last year, or the year before); anything
    older should be re-researched.

    Freshness is judged by the DATA's own period, not by when we happened to fetch it. The
    year is extracted by regex so prefixed/suffixed formats ('FY2025', '2024-Q4') all work.
    """
    match = re.search(r"(?:19|20)\d{2}", period or "")
    if not match:
        return False
    return int(match.group()) >= datetime.now(timezone.utc).year - 2


# ---------------------------------------------------------------------------
# TAXONOMY -- define the sub-industries under an industry group (ReAct + HITL).
# ---------------------------------------------------------------------------
async def propose_taxonomy(
    group_code: str,
    *,
    search: SearchClient,
    gics: StaticRepository[GicsReference],
    feedback: str | None = None,
    current: SubIndustryProposal | None = None,
) -> SubIndustryProposal:
    """Propose (or refine) the sub-industries for one industry group via the agent."""
    group = await gics.get(group_code)
    if group is None:
        raise ValueError(f"unknown industry group: {group_code}")
    return await propose_sub_industries(
        group.group_name, search=search, feedback=feedback, current=current
    )


async def save_taxonomy(
    group_code: str,
    findings: list[SubIndustryFinding],
    *,
    sub_industries: StaticRepository[SubIndustry],
) -> list[SubIndustry]:
    """Persist the approved sub-industries with surrogate codes (group_code + '-NN')."""
    saved: list[SubIndustry] = []
    for i, f in enumerate(findings, 1):
        sub = SubIndustry(
            sub_code=f"{group_code}-{i:02d}",
            group_code=group_code,
            name=f.name,
            definition=f.definition,
        )
        await sub_industries.upsert(sub)
        saved.append(sub)
    return saved


# ---------------------------------------------------------------------------
# ANALYZE -- fill market shares for a sector's sub-industries.
# ---------------------------------------------------------------------------
async def _ensure_company(name: str, *, companies: StaticRepository[Company]) -> str:
    """Return the surrogate company_code for `name`, registering it if new.

    NOTE: under the fan-out this read-then-write can race on the same new name (a harmless
    duplicate row at worst); acceptable for MVP, tighten with a UNIQUE(name) index later.
    """
    existing = await companies.list(name=name)
    if existing:
        return existing[0].company_code
    code = f"C{len(await companies.list()) + 1:04d}"
    await companies.upsert(Company(company_code=code, name=name))
    return code


async def analyze_sub_industry(
    sub: SubIndustry,
    *,
    search: SearchClient,
    companies: StaticRepository[Company],
    market_shares: TimeSeriesRepository[MarketShare],
    refresh: bool = False,
) -> list[MarketShare]:
    """Fan-out target: research one sub-industry's shares -> upsert companies + store rows.

    Freshness is judged by the DATA's own reporting period (`as_of`), not by when we ran:
    reuse the most recent stored period if it is still fresh, else re-research. Never raises
    (empty on failure); empty results are NOT stored (so a failed research re-runs next time).
    """
    try:
        if not refresh:
            history = await market_shares.history(sub.sub_code)
            if history:
                latest = max(r.period for r in history)
                if is_fresh(latest):
                    return [r for r in history if r.period == latest]
        result = await research_market_share(sub.name, search=search)
        if not result.shares:
            return []
        period = result.as_of.strip() or current_period()
        rows: list[MarketShare] = []
        for sh in result.shares:
            code = await _ensure_company(sh.company, companies=companies)
            rows.append(
                MarketShare(
                    sub_code=sub.sub_code,
                    company_code=code,
                    period=period,
                    percentage=sh.percentage,
                    source=sh.source,
                )
            )
        await market_shares.replace(sub.sub_code, period, rows)
        return rows
    except Exception:
        return []


async def shares_response(
    sub: SubIndustry,
    rows: list[MarketShare],
    *,
    companies: StaticRepository[Company],
) -> dict:
    """Shape one sub-industry's market-share rows into the API/FE dict (resolving names)."""
    names = {c.company_code: c.name for c in await companies.list()}
    return {
        "sub_code": sub.sub_code,
        "name": sub.name,
        "as_of": rows[0].period if rows else "",
        "shares": [
            {
                "company_code": m.company_code,
                "company_name": names.get(m.company_code, m.company_code),
                "percentage": m.percentage,
                "source": m.source,
            }
            for m in rows
        ],
    }


# ---------------------------------------------------------------------------
# COMPANY -- financials + portfolio. EDGAR (US-listed) is deterministic; else web fallback.
# ---------------------------------------------------------------------------
async def _replace_by_period(repo, parent: str, rows: list) -> None:
    """Group rows by period and replace each (parent, period) set atomically (idempotent)."""
    by_period: dict[str, list] = {}
    for r in rows:
        by_period.setdefault(r.period, []).append(r)
    for period, group in by_period.items():
        await repo.replace(parent, period, group)


async def _company_web_fallback(
    company_code: str,
    name: str,
    *,
    search: SearchClient,
    financials: TimeSeriesRepository[CompanyFinancials],
    portfolios: TimeSeriesRepository[CompanyPortfolio],
    refresh: bool = False,
) -> None:
    """Non-US / unlisted: company_agent researches financials + portfolio (freshness-gated).

    Web research is expensive, so reuse the most recent stored period while it's still fresh.
    Empty results are NOT stored (so a failed run re-researches next time).
    """
    if not refresh:
        history = await financials.history(company_code)
        if history and is_fresh(max(r.period for r in history)):
            return
    result = await research_company(name, search=search)
    if not (result.financials or result.portfolio):
        return
    period = result.as_of.strip() or current_period()
    fin_rows = [
        CompanyFinancials(
            company_code=company_code, period=period,
            account=f.account, amount=f.amount, source=f.source,
        )
        for f in result.financials
    ]
    seg_rows = [
        CompanyPortfolio(
            company_code=company_code, period=period,
            stream=s.stream, amount=s.amount, source=s.source,
        )
        for s in result.portfolio
    ]
    if fin_rows:
        await financials.replace(company_code, period, fin_rows)
    if seg_rows:
        await portfolios.replace(company_code, period, seg_rows)


async def analyze_company(
    name: str,
    *,
    edgar,
    companies: StaticRepository[Company],
    financials: TimeSeriesRepository[CompanyFinancials],
    portfolios: TimeSeriesRepository[CompanyPortfolio],
    search: SearchClient | None = None,
    refresh: bool = False,
) -> dict:
    """Analyze one company -> financials + portfolio, stored per fiscal period.

    US-listed (EDGAR CIK found) -> edgartools, deterministic. Otherwise -> web fallback
    (company_agent), when a `search` client is provided.
    """
    company_code = await _ensure_company(name, companies=companies)
    cik = edgar.lookup(name)
    if cik:
        fin_rows = [
            CompanyFinancials(
                company_code=company_code, period=f.period, account=f.key,
                amount=f.amount, source=f.source,
            )
            for f in edgar.financials(name)
        ]
        seg_rows = [
            CompanyPortfolio(
                company_code=company_code, period=f.period, stream=f.key,
                amount=f.amount, source=f.source,
            )
            for f in edgar.segments(name)
        ]
        await _replace_by_period(financials, company_code, fin_rows)
        await _replace_by_period(portfolios, company_code, seg_rows)
    elif search is not None:
        await _company_web_fallback(
            company_code, name, search=search,
            financials=financials, portfolios=portfolios, refresh=refresh,
        )
    fin = await financials.history(company_code)
    port = await portfolios.history(company_code)
    return {
        "company_code": company_code,
        "name": name,
        "cik": cik,
        "financials": [r.model_dump() for r in fin],
        "portfolio": [r.model_dump() for r in port],
    }
