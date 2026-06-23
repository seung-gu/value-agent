"""Orchestrator (application layer) -- two-stage progressive refinement for one sector.

Stage 1 (analyze_sector): the big picture, shallow and fast.
  sector_agent          -> sub-industries (name + optional market_size) + sector metrics
    └─ fan-out: sub_industry_agent per sub-industry -> company market shares
  Company portfolios are NOT fetched here -- empty spots are left as-is.

Stage 2 (refine_*): fill one spot the user asked about, on demand.

Depends on PORTS (the repository interfaces + SearchClient), never on adapters -- the api
composition root injects the concrete sqlite repos / Serper client. Each function does
read-before / write-after against its repository; `refresh=True` skips the read (re-research)
but still writes. Fan-outs are graceful: one failing agent yields an empty result.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agents.company_agent import research_company
from agents.sub_industry_agent import research_sub_industry
from agents.sector_agent import identify_sub_industries
from domain.company import CompanyPortfolio
from domain.sector import SectorAnalysis
from domain.sub_industry import SubIndustry
from ports.search_client import SearchClient
from ports.repository import Repository


def current_period() -> str:
    """Today's freshness bucket, e.g. '2026-Q2'. Used as the `period` key for current data."""
    now = datetime.now(timezone.utc)
    return f"{now.year}-Q{(now.month - 1) // 3 + 1}"


async def _safe_sub_industry(
    name: str, *, search: SearchClient, repo: Repository[SubIndustry], refresh: bool = False
) -> SubIndustry:
    """Read-before/write-after one sub-industry's shares; never raise (empty on failure)."""
    try:
        period = current_period()
        if not refresh:
            hit = await repo.get(name, period)
            if hit is not None:
                return hit
        result = await research_sub_industry(name, search=search)
        if result.companies:  # don't cache empty/failed research -> re-research next time
            await repo.save(result, period)
        return result
    except Exception:
        return SubIndustry(name=name)


async def _safe_portfolio(
    name: str, *, search: SearchClient, repo: Repository[CompanyPortfolio], refresh: bool = False
) -> CompanyPortfolio:
    """Read-before/write-after one company's portfolio; never raise (empty on failure)."""
    try:
        period = current_period()
        if not refresh:
            hit = await repo.get(name, period)
            if hit is not None:
                return hit
        result = await research_company(name, search=search)
        if result.portfolio:  # don't cache empty/failed research -> re-research next time
            await repo.save(result, period)
        return result
    except Exception:
        return CompanyPortfolio(name=name)


# ---------------------------------------------------------------------------
# Stage 1 -- the big picture (sector + sub-industry shares). Shallow & fast.
# ---------------------------------------------------------------------------
async def analyze_sector(
    sector: str,
    *,
    search: SearchClient,
    sectors: Repository[SectorAnalysis],
    sub_industries: Repository[SubIndustry],
    refresh: bool = False,
) -> SectorAnalysis:
    """Sector metrics + sub-industries + their company shares. Company portfolios left empty."""
    period = current_period()
    base = None if refresh else await sectors.get(sector, period)
    if base is None:
        base = await identify_sub_industries(sector, search=search)
        await sectors.save(base, period)

    # Fan-out: per-sub-industry company shares (parallel, graceful)
    filled = await asyncio.gather(
        *[
            _safe_sub_industry(sub.name, search=search, repo=sub_industries, refresh=refresh)
            for sub in base.sub_industries
        ]
    )
    # sub_industry_agent fills companies but not market_size -- carry it over from stage 1
    for original, researched in zip(base.sub_industries, filled):
        researched.market_size = original.market_size

    return SectorAnalysis(
        sector=base.sector,
        market_size=base.market_size,
        cagr=base.cagr,
        potential_score=base.potential_score,
        sub_industries=list(filled),
        company_portfolios=[],  # filled on demand via refine_company (stage 2)
        key_drivers=base.key_drivers,
        sources=base.sources,
        confidence=base.confidence,
    )


# ---------------------------------------------------------------------------
# Stage 2 -- refine one spot on demand (driven by the user / FE).
# ---------------------------------------------------------------------------
async def refine_sub_industry(
    name: str, *, search: SearchClient, repo: Repository[SubIndustry], refresh: bool = False
) -> SubIndustry:
    """(Re)research one sub-industry's company shares -- refresh=True bypasses the cache read."""
    return await _safe_sub_industry(name, search=search, repo=repo, refresh=refresh)


async def refine_company(
    name: str, *, search: SearchClient, repo: Repository[CompanyPortfolio], refresh: bool = False
) -> CompanyPortfolio:
    """Research one company's business portfolio -- refresh=True bypasses the cache read."""
    return await _safe_portfolio(name, search=search, repo=repo, refresh=refresh)
