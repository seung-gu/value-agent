"""sector_agent 평가 셋업 (개발 안전망 / 회귀 테스트).

골든 섹터셋에 대해 sector_agent를 돌리고 다층으로 평가한다:
- 커스텀(deterministic, 공짜): sources·top_companies·score 충족 여부
- HasMatchingSpan: get_today·web_search를 실제로 호출했는지 (logfire span 기반)
- LLMJudge: 출처 신뢰도 / 데이터 최신성 (차원별로 분리)

실행:
    uv run eval_sectors.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import (
    Evaluator,
    EvaluatorContext,
    HasMatchingSpan,
    IsInstance,
    LLMJudge,
)

from search import SerperClient
from sector_agent import Deps, SectorAnalysis, sector_agent

JUDGE_MODEL = "anthropic:claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Task — 평가 대상: 섹터명을 받아 SectorAnalysis를 반환
# ---------------------------------------------------------------------------
async def analyze(sector: str) -> SectorAnalysis:
    async with httpx.AsyncClient() as client:
        deps = Deps(search=SerperClient(os.environ["SERPER_API_KEY"], client))
        result = await sector_agent.run(f"Analyze the {sector} sector.", deps=deps)
        return result.output


# ---------------------------------------------------------------------------
# 커스텀 evaluator — 근거가 충분한지 (LLM 없이, 공짜·결정적)
# ---------------------------------------------------------------------------
@dataclass
class HasEvidence(Evaluator):
    min_sources: int = 3

    def evaluate(self, ctx: EvaluatorContext) -> dict[str, bool]:
        out: SectorAnalysis = ctx.output
        return {
            "has_sources": len(out.sources) >= self.min_sources,
            "has_companies": len(out.top_companies) >= 1,
            "score_in_range": 0.0 <= out.potential_score <= 100.0,
        }


# ---------------------------------------------------------------------------
# 골든 데이터셋
# ---------------------------------------------------------------------------
dataset = Dataset(
    name="sector_analysis",
    cases=[
        Case(name="information_technology", inputs="Information Technology"),
        Case(name="health_care", inputs="Health Care"),
        Case(name="energy", inputs="Energy"),
    ],
    evaluators=[
        IsInstance(type_name="SectorAnalysis"),
        HasEvidence(),
        # 행동 검증: 날짜를 먼저 인지하고 검색을 실제로 했는가 (logfire span)
        HasMatchingSpan(
            query={"has_attributes": {"gen_ai.tool.name": "get_today"}},
            evaluation_name="used_get_today",
        ),
        HasMatchingSpan(
            query={"has_attributes": {"gen_ai.tool.name": "web_search"}},
            evaluation_name="used_web_search",
        ),
        # 품질 검증: 차원별로 분리된 LLMJudge
        LLMJudge(
            rubric=(
                "The `sources` are from reputable outlets (e.g. Gartner, Reuters, SEC, "
                "Bloomberg, established market-research firms or company IR pages), and "
                "the market_size/cagr figures are attributable to them — not vague or invented."
            ),
            model=JUDGE_MODEL,
            model_settings={"temperature": 0.0},
            assertion={"evaluation_name": "sources_reputable", "include_reason": True},
        ),
        LLMJudge(
            rubric=(
                "The figures reflect recent data (roughly within the current quarter "
                "+/- 4 quarters), not stale numbers that are several years old."
            ),
            model=JUDGE_MODEL,
            model_settings={"temperature": 0.0},
            assertion={"evaluation_name": "data_is_recent", "include_reason": True},
        ),
    ],
)


if __name__ == "__main__":
    report = dataset.evaluate_sync(analyze)
    report.print(include_input=False, include_output=False)
