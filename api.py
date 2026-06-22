"""FastAPI -- backend exposing the orchestrator over HTTP. Called by the FE (Expo).

Composition root: wires the concrete adapters (sqlite repositories, blob store, Serper client)
and injects them into the orchestrator (which only knows the ports).

- GET  /sectors : list of 11 GICS sectors (for the FE dropdown)
- POST /analyze : {"sector": "..."} -> SectorAnalysis
- POST /refine/sub-industry , /refine/company : fill one spot on demand
- GET  /        : health check

Run:
    uv run uvicorn api:app --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from adapters.local.blob_store import LocalBlobStore
from adapters.serper.search_client import SerperClient
from adapters.sqlite import SqliteStorage
from domain import CompanyPortfolio, SectorAnalysis, SubIndustry
from orchestrator import analyze_sector, refine_company, refine_sub_industry

# Where the data lives. Local dev: ./data. Railway: set DATA_DIR=/data (the mounted volume).
DATA_DIR = os.environ.get("DATA_DIR", "data")

GICS_SECTORS = [
    "Information Technology",
    "Health Care",
    "Financials",
    "Consumer Discretionary",
    "Communication Services",
    "Industrials",
    "Consumer Staples",
    "Energy",
    "Utilities",
    "Real Estate",
    "Materials",
]


def _make_blobs(http: httpx.AsyncClient):
    """Raw store: R2 when its env vars are set (prod), else local files on the volume."""
    if os.environ.get("R2_BUCKET"):
        from adapters.r2.blob_store import R2BlobStore  # lazy -- only when R2 is configured

        return R2BlobStore.from_env(http)
    return LocalBlobStore(f"{DATA_DIR}/raw")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One http client + one storage (sqlite repos) for the app lifetime, shared across requests.
    app.state.http = httpx.AsyncClient()
    app.state.storage = await SqliteStorage.open(f"{DATA_DIR}/cache.db")
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


class AnalyzeRequest(BaseModel):
    sector: str
    refresh: bool = False  # force re-research, bypassing the cache read


@app.get("/sectors")
def list_sectors() -> list[str]:
    """The 11 GICS sectors for the FE dropdown."""
    return GICS_SECTORS


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> SectorAnalysis:
    """Sector -> sub-industry shares (company portfolios filled on demand in stage 2)."""
    return await analyze_sector(
        req.sector,
        search=_search(),
        sectors=app.state.storage.sectors,
        sub_industries=app.state.storage.sub_industries,
        refresh=req.refresh,
    )


class RefineRequest(BaseModel):
    name: str  # a sub-industry name or a company name
    refresh: bool = False  # force re-research, bypassing the cache read


@app.post("/refine/sub-industry")
async def refine_sub_industry_ep(req: RefineRequest) -> SubIndustry:
    """Stage 2 -- fill one sub-industry's company shares on demand (e.g. an empty one)."""
    return await refine_sub_industry(
        req.name, search=_search(), repo=app.state.storage.sub_industries, refresh=req.refresh
    )


@app.post("/refine/company")
async def refine_company_ep(req: RefineRequest) -> CompanyPortfolio:
    """Stage 2 -- research one company's business portfolio on demand."""
    return await refine_company(
        req.name, search=_search(), repo=app.state.storage.companies, refresh=req.refresh
    )


@app.get("/")
def health() -> dict[str, str]:
    return {"status": "ok"}
