"""Orchestrator (application layer) -- wires agents + repositories into the analysis flow.

Two flows:
- TAXONOMY (define sub-industries): `propose_taxonomy` runs sub_industry_agent (ReAct) for an
  industry group; `save_taxonomy` persists the approved list with surrogate sub_codes. The
  human-in-the-loop refine happens at the API layer (propose -> refine -> save).
- ANALYZE (fill market shares): for a sector, read its industry groups -> their (already
  defined) sub-industries -> fan out market_share_agent per sub-industry -> upsert the
  companies (surrogate codes) + store market_share rows for the period.

Depends on the repository PORTS, never on adapters. `period` is the freshness bucket.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agents.company_agent import research_portfolio
from agents.market_share_agent import research_market_share
from agents.sub_industry_agent import (
    SubIndustryFinding,
    SubIndustryProposal,
    propose_sub_industries,
)
from domain import Company, CompanyPortfolio, GicsReference, MarketShare, SubIndustry
from ports.repository import StaticRepository, TimeSeriesRepository
from ports.search_client import SearchClient


def current_period() -> str:
    """Today's freshness bucket, e.g. '2026-Q2'."""
    now = datetime.now(timezone.utc)
    return f"{now.year}-Q{(now.month - 1) // 3 + 1}"


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
    period: str,
    companies: StaticRepository[Company],
    market_shares: TimeSeriesRepository[MarketShare],
    refresh: bool = False,
) -> list[MarketShare]:
    """Fan-out target: research one sub-industry's shares -> upsert companies + store rows.

    Read-before/write-after; never raises (empty on failure). Empty results are NOT stored
    (so a failed research re-runs next time).
    """
    try:
        if not refresh:
            hit = await market_shares.get(sub.sub_code, period)
            if hit:
                return hit
        result = await research_market_share(sub.name, search=search)
        if not result.shares:
            return []
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


async def analyze_sector(
    sector_code: str,
    *,
    search: SearchClient,
    gics: StaticRepository[GicsReference],
    sub_industries: StaticRepository[SubIndustry],
    companies: StaticRepository[Company],
    market_shares: TimeSeriesRepository[MarketShare],
    refresh: bool = False,
) -> dict:
    """For a sector: its industry groups -> their sub-industries -> fan out share research.

    Returns a nested dict (sector -> groups -> sub-industries -> shares) for the API/FE.
    Sub-industries must already be defined (via the taxonomy flow); groups with none are empty.
    """
    period = current_period()
    groups = await gics.list(sector_code=sector_code)
    # pass 1: research every group's sub-industries (registers companies as a side effect)
    analyzed: list[tuple[GicsReference, list[SubIndustry], list[list[MarketShare]]]] = []
    for group in groups:
        subs = await sub_industries.list(group_code=group.group_code)
        filled = await asyncio.gather(
            *[
                analyze_sub_industry(
                    s,
                    search=search,
                    period=period,
                    companies=companies,
                    market_shares=market_shares,
                    refresh=refresh,
                )
                for s in subs
            ]
        )
        analyzed.append((group, subs, filled))
    # pass 2: resolve company names (all registered now) + build the response
    names = {c.company_code: c.name for c in await companies.list()}
    out: dict = {"sector_code": sector_code, "period": period, "groups": []}
    for group, subs, filled in analyzed:
        out["groups"].append(
            {
                "group_code": group.group_code,
                "group_name": group.group_name,
                "sub_industries": [
                    {
                        "sub_code": s.sub_code,
                        "name": s.name,
                        "shares": [
                            {
                                "company_code": m.company_code,
                                "company_name": names.get(m.company_code, m.company_code),
                                "percentage": m.percentage,
                                "source": m.source,
                            }
                            for m in shares
                        ],
                    }
                    for s, shares in zip(subs, filled)
                ],
            }
        )
    return out


# ---------------------------------------------------------------------------
# PORTFOLIO -- one company's revenue breakdown (same breakdown pattern as market_share).
# ---------------------------------------------------------------------------
async def analyze_company_portfolio(
    company_code: str,
    name: str,
    *,
    search: SearchClient,
    portfolios: TimeSeriesRepository[CompanyPortfolio],
    period: str | None = None,
    refresh: bool = False,
) -> list[CompanyPortfolio]:
    """Research one company's revenue segments -> store company_portfolio rows for the period."""
    period = period or current_period()
    try:
        if not refresh:
            hit = await portfolios.get(company_code, period)
            if hit:
                return hit
        result = await research_portfolio(name, search=search)
        if not result.segments:
            return []
        rows = [
            CompanyPortfolio(
                company_code=company_code,
                period=period,
                segment=s.segment,
                percentage=s.percentage,
                source=s.source,
            )
            for s in result.segments
        ]
        await portfolios.replace(company_code, period, rows)
        return rows
    except Exception:
        return []
