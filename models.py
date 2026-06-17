"""공유 데이터 모델 — 여러 모듈(sector_agent, verifier, eval)이 import한다.

별도 파일로 둬서 순환 import를 피한다
(sector_agent ↔ verifier가 둘 다 SectorAnalysis를 여기서 가져옴).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompetitorCompany(BaseModel):
    name: str
    reason: str  # 이 섹터에서 왜 경쟁력 있는지


class SectorAnalysis(BaseModel):
    sector: str                       # GICS 섹터명
    market_size: str                  # 값 + 연도 (예: "$1.77B (2026)")
    cagr: str                         # % + 기간 (예: "23.8% (2026-2032)")
    potential_score: float = Field(ge=0, le=100)  # 섹터 랭킹/비교용 점수
    top_companies: list[CompetitorCompany]        # 발굴된 경쟁 기업
    key_drivers: list[str] = Field(default_factory=list)        # 성장 동인
    extra_metrics: dict[str, str] = Field(default_factory=dict)  # agent 자율 추가 지표
    sources: list[str] = Field(default_factory=list)            # 출처 URL (환각 방지)
    confidence: float = Field(ge=0, le=1, default=0.5)
