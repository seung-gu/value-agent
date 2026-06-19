"""Orchestrator -- two-stage progressive refinement for one sector.

Stage 1 (analyze_sector): the big picture, shallow and fast.
  sector_agent          -> sub-industries (name + optional market_size) + sector metrics
    └─ fan-out: industry_agent per sub-industry -> company market shares
  Company portfolios are NOT fetched here -- empty spots are left as-is.

Stage 2 (refine_*): fill one spot the user asked about, on demand.
  refine_sub_industry(name) -> (re)research one sub-industry's company shares
  refine_company(name)      -> research one company's portfolio

Why: a single pass can't fill everything well, and forcing it makes agents loop on
data they can't find (search blow-up). So stage 1 stays shallow; the user drives
stage 2 only where they care. Company is excluded from stage 1 precisely because
fanning out to ~10 companies at once is what blew up the search count.

Programmatic orchestration (deterministic control flow), not LLM-driven hand-off.
No shared usage: PydanticAI's default request_limit=50 is per-run, so sharing one
usage would make the whole fan-out hit the limit together. Fan-outs are graceful --
one failing agent yields an empty result instead of killing the run.
"""

from __future__ import annotations

import asyncio

from company_agent import research_company
from industry_agent import research_sub_industry
from models import CompanyPortfolio, SectorAnalysis, SubIndustry
from search import SearchClient

from sector_agent import identify_sub_industries


async def _safe_sub_industry(name: str, *, search: SearchClient) -> SubIndustry:
    """research_sub_industry, but never raise -- return an empty SubIndustry on failure."""
    try:
        return await research_sub_industry(name, search=search)
    except Exception:
        return SubIndustry(name=name)


async def _safe_portfolio(name: str, *, search: SearchClient) -> CompanyPortfolio:
    """research_company, but never raise -- return an empty CompanyPortfolio on failure."""
    try:
        return await research_company(name, search=search)
    except Exception:
        return CompanyPortfolio(name=name)


# ---------------------------------------------------------------------------
# Stage 1 -- the big picture (sector + sub-industry shares). Shallow & fast.
# ---------------------------------------------------------------------------
async def analyze_sector(sector: str, *, search: SearchClient) -> SectorAnalysis:
    """Sector metrics + sub-industries + their company shares. Company portfolios left empty."""
    base = await identify_sub_industries(sector, search=search)

    # Fan-out: per-sub-industry company shares (parallel, graceful)
    filled = await asyncio.gather(
        *[_safe_sub_industry(sub.name, search=search) for sub in base.sub_industries]
    )
    # industry_agent fills companies but not market_size -- carry it over from stage 1
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
async def refine_sub_industry(name: str, *, search: SearchClient) -> SubIndustry:
    """(Re)research one sub-industry's company shares -- e.g. an empty one the user clicked."""
    return await _safe_sub_industry(name, search=search)


async def refine_company(name: str, *, search: SearchClient) -> CompanyPortfolio:
    """Research one company's business portfolio -- e.g. a company the user clicked."""
    return await _safe_portfolio(name, search=search)
