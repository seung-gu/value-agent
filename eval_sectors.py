"""sector_agent evaluation setup (dev safety net / regression tests).

Runs sector_agent over a golden sector set and evaluates it in layers:
- custom (deterministic, free): whether sources / top_companies / score are satisfied
- HasMatchingSpan: whether get_today / web_search were actually called (logfire spans)
- LLMJudge: source reputation / data recency (split per dimension)

Run:
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
# Task -- the thing under eval: takes a sector name, returns SectorAnalysis
# ---------------------------------------------------------------------------
async def analyze(sector: str) -> SectorAnalysis:
    async with httpx.AsyncClient() as client:
        deps = Deps(search=SerperClient(os.environ["SERPER_API_KEY"], client))
        result = await sector_agent.run(f"Analyze the {sector} sector.", deps=deps)
        return result.output


# ---------------------------------------------------------------------------
# Custom evaluator -- whether evidence is sufficient (no LLM, free & deterministic)
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
# Golden dataset
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
        # Behavior check: did it anchor the date first and actually search (logfire span)
        HasMatchingSpan(
            query={"has_attributes": {"gen_ai.tool.name": "get_today"}},
            evaluation_name="used_get_today",
        ),
        HasMatchingSpan(
            query={"has_attributes": {"gen_ai.tool.name": "web_search"}},
            evaluation_name="used_web_search",
        ),
        # Quality check: LLMJudge split per dimension
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
