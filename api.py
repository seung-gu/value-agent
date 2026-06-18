"""FastAPI — sector_agent를 HTTP로 노출하는 백엔드. FE(Expo)가 호출한다.

브라우저/앱은 Python agent를 직접 못 돌리고, API 키도 클라이언트에 노출하면 안 되므로,
키 보관 + agent 실행은 이 서버가 하고 FE는 결과만 받아간다.

- GET  /sectors : GICS 11개 섹터 목록 (FE 드롭다운용)
- POST /analyze : {"sector": "..."} → SectorAnalysis (sector_agent 실행)
- GET  /        : health check

실행:
    uv run uvicorn api:app --reload          # 개발 (http://127.0.0.1:8000, /docs 에 Swagger)
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
    # http client를 앱 수명 동안 재사용 (요청마다 새로 만들지 않게)
    app.state.http = httpx.AsyncClient()
    yield
    await app.state.http.aclose()


app = FastAPI(title="value-agent API", lifespan=lifespan)

# FE(Expo)는 다른 origin에서 호출하므로 CORS 허용. 운영 시엔 allow_origins를 좁힐 것.
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
    """FE 드롭다운용 GICS 11개 섹터."""
    return GICS_SECTORS


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> SectorAnalysis:
    """섹터명을 받아 sector_agent로 분석 → SectorAnalysis 반환.

    sector_agent의 output_validator(형식 + judge 품질)가 자동으로 돌고,
    실패 시 ModelRetry로 재조사된 결과가 나온다.
    """
    deps = Deps(search=SerperClient(os.environ["SERPER_API_KEY"], app.state.http))
    result = await sector_agent.run(f"Analyze the {req.sector} sector.", deps=deps)
    return result.output


@app.get("/")
def health() -> dict[str, str]:
    return {"status": "ok"}
