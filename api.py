"""FastAPI -- backend exposing the orchestrator over HTTP. Called by the FE (Expo).

Composition root: wires the concrete adapters (sqlite repositories, blob store, Serper client)
and injects them into the orchestrator (which knows only the ports). On boot it seeds the 25
GICS industry groups.

- GET  /sectors             : 11 GICS sectors (from gics_reference)
- GET  /groups?sector_code  : industry groups under a sector
- POST /taxonomy/propose    : agent proposes sub-industries for a group (HITL)
- POST /taxonomy/refine     : revise a proposal with feedback (current + feedback)
- POST /taxonomy/save       : persist approved sub-industries (surrogate codes)
- GET  /taxonomy?group_code : stored sub-industries for a group
- POST /analyze             : sector -> groups -> sub-industries -> market shares
- GET  /                    : health

Run:
    uv run uvicorn api:app --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import httpx
import logfire
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()
logfire.instrument_httpx()

from adapters.local.blob_store import LocalBlobStore
from adapters.serper.search_client import SerperClient
from adapters.sqlite import SqliteStorage
from adapters.sqlite.seed import seed_gics
from agents.sub_industry_agent import SubIndustryFinding, SubIndustryProposal
from domain import CompanyPortfolio, GicsReference, SubIndustry
from orchestrator import (
    analyze_company_portfolio,
    analyze_sector,
    propose_taxonomy,
    save_taxonomy,
)

# Where the data lives. Local dev: ./data. Railway: set DATA_DIR=/data (the mounted volume).
DATA_DIR = os.environ.get("DATA_DIR", "data")


def _make_blobs(http: httpx.AsyncClient):
    """Raw store: R2 when its env vars are set (prod), else local files on the volume."""
    if os.environ.get("R2_BUCKET"):
        from adapters.r2.blob_store import R2BlobStore  # lazy -- only when R2 is configured

        return R2BlobStore.from_env(http)
    return LocalBlobStore(f"{DATA_DIR}/raw")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One http client + one storage for the app lifetime, shared across requests.
    app.state.http = httpx.AsyncClient()
    app.state.storage = await SqliteStorage.open(f"{DATA_DIR}/cache.db")
    await seed_gics(app.state.storage.gics)  # 25 GICS industry groups, idempotent
    app.state.blobs = _make_blobs(app.state.http)
    yield
    await app.state.storage.close()
    await app.state.http.aclose()


app = FastAPI(title="value-agent API", lifespan=lifespan)

# The FE (Expo) calls from a different origin, so allow CORS. Narrow allow_origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _search() -> SerperClient:
    """A Serper client with scrape caching wired to the shared blob store."""
    return SerperClient(os.environ["SERPER_API_KEY"], app.state.http, blobs=app.state.blobs)


# --- reference -------------------------------------------------------------------------
@app.get("/sectors")
async def list_sectors() -> list[dict]:
    """The 11 GICS sectors (distinct from gics_reference), for the FE dropdown."""
    groups = await app.state.storage.gics.list()
    seen: dict[str, str] = {g.sector_code: g.sector_name for g in groups}
    return [{"sector_code": c, "sector_name": seen[c]} for c in sorted(seen)]


@app.get("/groups")
async def list_groups(sector_code: str) -> list[GicsReference]:
    """The industry groups under a sector."""
    return await app.state.storage.gics.list(sector_code=sector_code)


# --- taxonomy (HITL: propose -> refine -> save) ----------------------------------------
class ProposeRequest(BaseModel):
    group_code: str
    feedback: str | None = None
    current: SubIndustryProposal | None = None  # prior list, when refining


@app.post("/taxonomy/propose")
async def taxonomy_propose(req: ProposeRequest) -> SubIndustryProposal:
    """Agent proposes the sub-industries for a group (no save -- this is the HITL draft)."""
    return await propose_taxonomy(
        req.group_code,
        search=_search(),
        gics=app.state.storage.gics,
        feedback=req.feedback,
        current=req.current,
    )


@app.post("/taxonomy/refine")
async def taxonomy_refine(req: ProposeRequest) -> SubIndustryProposal:
    """Revise a proposal given the user's feedback + current list (stateless HITL step)."""
    return await propose_taxonomy(
        req.group_code,
        search=_search(),
        gics=app.state.storage.gics,
        feedback=req.feedback,
        current=req.current,
    )


class SaveRequest(BaseModel):
    group_code: str
    findings: list[SubIndustryFinding]


@app.post("/taxonomy/save")
async def taxonomy_save(req: SaveRequest) -> list[SubIndustry]:
    """Persist the approved (or manually edited) sub-industries with surrogate codes."""
    return await save_taxonomy(
        req.group_code, req.findings, sub_industries=app.state.storage.sub_industries
    )


@app.get("/taxonomy")
async def taxonomy_get(group_code: str) -> list[SubIndustry]:
    """The stored sub-industries for a group."""
    return await app.state.storage.sub_industries.list(group_code=group_code)


# --- analyze ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    sector_code: str
    refresh: bool = False  # force re-research, bypassing the cache read


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    """Sector -> industry groups -> their sub-industries -> company market shares."""
    s = app.state.storage
    return await analyze_sector(
        req.sector_code,
        search=_search(),
        gics=s.gics,
        sub_industries=s.sub_industries,
        companies=s.companies,
        market_shares=s.market_shares,
        refresh=req.refresh,
    )


class PortfolioRequest(BaseModel):
    company_code: str
    refresh: bool = False


@app.post("/company/portfolio")
async def company_portfolio(req: PortfolioRequest) -> list[CompanyPortfolio]:
    """One company's revenue breakdown by segment (the portfolio pie), stored per period."""
    company = await app.state.storage.companies.get(req.company_code)
    if company is None:
        raise HTTPException(status_code=404, detail=f"unknown company: {req.company_code}")
    return await analyze_company_portfolio(
        req.company_code,
        company.name,
        search=_search(),
        portfolios=app.state.storage.portfolios,
        refresh=req.refresh,
    )


@app.get("/")
def health() -> dict[str, str]:
    return {"status": "ok"}
