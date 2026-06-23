"""company_agent eval -- local quality / regression harness.

Same measurement loop as eval_market_share, for the revenue-portfolio agent: run it over a
golden company set, score in layers, tune until the scores climb.

Run (from repo root):
    uv run python -m evals.eval_company
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
import logfire
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import (
    Evaluator,
    EvaluatorContext,
    HasMatchingSpan,
    IsInstance,
    LLMJudge,
)

from adapters.serper.search_client import SerperClient
from agents.company_agent import PortfolioResult, research_portfolio

# Capture pydantic-ai spans so HasMatchingSpan can see tool calls (no logfire token needed).
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()

JUDGE_MODEL = "anthropic:claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Task under eval -- a company name -> the agent's PortfolioResult
# ---------------------------------------------------------------------------
async def run_portfolio(company: str) -> PortfolioResult:
    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        return await research_portfolio(company, search=search)


# ---------------------------------------------------------------------------
# Deterministic evaluator -- free checks on the segment list
# ---------------------------------------------------------------------------
@dataclass
class PortfolioQuality(Evaluator):
    min_named: int = 2  # at least this many real segments, not just one + Others

    def evaluate(self, ctx: EvaluatorContext) -> dict[str, bool]:
        out: PortfolioResult = ctx.output
        segs = out.segments
        total = sum(s.percentage for s in segs)
        named = [s for s in segs if s.segment.strip().lower() != "others"]
        return {
            "non_empty": len(segs) > 0,
            "sums_to_100": bool(segs) and 90.0 <= total <= 110.0,
            "has_as_of": bool(out.as_of.strip()),
            "all_sourced": bool(segs) and all(s.source.strip() for s in segs),
            "coverage_ok": len(named) >= self.min_named,
        }


# ---------------------------------------------------------------------------
# Golden dataset
# ---------------------------------------------------------------------------
dataset = Dataset(
    name="company_portfolio",
    cases=[
        Case(name="apple", inputs="Apple"),
        Case(name="microsoft", inputs="Microsoft"),
        Case(name="alphabet", inputs="Alphabet"),
        Case(name="samsung_electronics", inputs="Samsung Electronics"),
    ],
    evaluators=[
        IsInstance(type_name="PortfolioResult"),
        PortfolioQuality(),
        # Behavior: did it actually READ a filing, not just search?
        HasMatchingSpan(
            query={"has_attributes": {"gen_ai.tool.name": "web_read"}},
            evaluation_name="used_web_read",
        ),
        # Quality: coverage + sourcing + recency in one judge verdict.
        LLMJudge(
            rubric=(
                "`segments` covers the company's main reported business segments, sums to "
                "~100, every segment cites a reputable source (company 10-K/10-Q/IR, Reuters, "
                "Bloomberg), and `as_of` is a recent fiscal period (within ~1 year)."
            ),
            model=JUDGE_MODEL,
            model_settings={"temperature": 0.0},
            assertion={"evaluation_name": "portfolio_quality", "include_reason": True},
        ),
    ],
)


if __name__ == "__main__":
    report = dataset.evaluate_sync(run_portfolio)
    report.print(include_input=False, include_output=False)
