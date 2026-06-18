"""FastAPI -- backend exposing sector_agent over HTTP. Called by the FE (Expo).

Browsers/apps can't run the Python agent directly, and API keys must not be exposed
to the client, so this server holds the keys + runs the agent and the FE only receives
the result.

- GET  /sectors : list of 11 GICS sectors (for the FE dropdown)
- POST /analyze : {"sector": "..."} -> SectorAnalysis (runs sector_agent)
- GET  /        : health check

Run:
    uv run uvicorn api:app --reload          # dev (http://127.0.0.1:8000, Swagger at /docs)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import SectorAnalysis
from search import SerperClient
from sector_agent import Deps, sector_agent

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reuse one http client for the app lifetime (don't recreate per request).
    app.state.http = httpx.AsyncClient()
    yield
    await app.state.http.aclose()


app = FastAPI(title="value-agent API", lifespan=lifespan)

# The FE (Expo) calls from a different origin, so allow CORS. Narrow allow_origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    sector: str


@app.get("/sectors")
def list_sectors() -> list[str]:
    """The 11 GICS sectors for the FE dropdown."""
    return GICS_SECTORS


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> SectorAnalysis:
    """Take a sector name, analyze with sector_agent -> return SectorAnalysis.

    sector_agent's output_validators (format + judge quality) run automatically;
    on failure, ModelRetry produces a re-analyzed result.
    """
    deps = Deps(search=SerperClient(os.environ["SERPER_API_KEY"], app.state.http))
    result = await sector_agent.run(f"Analyze the {req.sector} sector.", deps=deps)
    return result.output


@app.get("/")
def health() -> dict[str, str]:
    return {"status": "ok"}
