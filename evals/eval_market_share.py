"""market_share_agent eval -- local quality / regression harness.

The measurement loop for hardening the agent: run it over a golden market set and score
it in layers, then tune prompt / rubric / validators until the scores climb.

Layers:
- ShareQuality (deterministic, free): non-empty, sums to ~100, `as_of` present, every entry
  sourced, and coverage >= 3 named players (catches "one leader + Others" laziness).
- HasMatchingSpan: web_read was ACTUALLY called -- i.e. it read a page, not just searched.
- LLMJudge: coverage + sourcing + recency, one verdict with a reason.

Run (from repo root):
    uv run python -m evals.eval_market_share
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
from agents.market_share_agent import MarketShareResult, research_market_share

# Capture pydantic-ai spans so HasMatchingSpan can see tool calls (no logfire token needed).
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()

JUDGE_MODEL = "anthropic:claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Task under eval -- a market name -> the agent's MarketShareResult
# ---------------------------------------------------------------------------
async def run_share(market: str) -> MarketShareResult:
    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        return await research_market_share(market, search=search)


# ---------------------------------------------------------------------------
# Deterministic evaluator -- free checks on the share list
# ---------------------------------------------------------------------------
@dataclass
class ShareQuality(Evaluator):
    min_named: int = 3  # at least this many real players, not just leader + Others

    def evaluate(self, ctx: EvaluatorContext) -> dict[str, bool]:
        out: MarketShareResult = ctx.output
        shares = out.shares
        total = sum(s.percentage for s in shares)
        named = [s for s in shares if s.company.strip().lower() != "others"]
        return {
            "non_empty": len(shares) > 0,
            "sums_to_100": bool(shares) and 90.0 <= total <= 110.0,
            "has_as_of": bool(out.as_of.strip()),
            "all_sourced": bool(shares) and all(s.source.strip() for s in shares),
            "coverage_ok": len(named) >= self.min_named,
        }


# ---------------------------------------------------------------------------
# Golden dataset
# ---------------------------------------------------------------------------
dataset = Dataset(
    name="market_share",
    cases=[
        Case(name="foundry", inputs="semiconductor foundry"),
        Case(name="dram", inputs="DRAM memory"),
        Case(name="smartphone", inputs="smartphone"),
        Case(name="cloud_infra", inputs="cloud infrastructure services"),
    ],
    evaluators=[
        IsInstance(type_name="MarketShareResult"),
        ShareQuality(),
        # Behavior: did it actually READ a page, not just search?
        HasMatchingSpan(
            query={"has_attributes": {"gen_ai.tool.name": "web_read"}},
            evaluation_name="used_web_read",
        ),
        # Quality: coverage + sourcing + recency in one judge verdict.
        LLMJudge(
            rubric=(
                "`shares` covers the real major players of this market (not just one leader "
                "plus 'Others'), sums to ~100, every entry cites a reputable source "
                "(IDC/Gartner/Counterpoint/TrendForce/Canalys/Omdia/Statista/company "
                "filings), and `as_of` is a recent reporting period (within ~2 years)."
            ),
            model=JUDGE_MODEL,
            model_settings={"temperature": 0.0},
            assertion={"evaluation_name": "share_quality", "include_reason": True},
        ),
    ],
)


if __name__ == "__main__":
    report = dataset.evaluate_sync(run_share)
    report.print(include_input=False, include_output=False)
