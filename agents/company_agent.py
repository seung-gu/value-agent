"""Company portfolio agent -- researches ONE company's revenue breakdown by segment.

Given a company name, finds how its revenue splits by business segment (e.g. Cloud,
Advertising, Devices) as source-backed %s, returned as `SegmentFinding`s. The orchestrator
turns these into company_portfolio rows -- the SAME breakdown pattern as market_share
(a parent split into parts that sum to ~100% per period).

Two-layer validation (both @output_validator -> ModelRetry, retries=2):
- Layer 1 (format): percentages 0-100 and sum to ~100 (deterministic, free).
- Layer 2 (quality): pass the portfolio rubric to the generic judge.

Run:
    uv run python -m agents.company_agent
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.usage import RunUsage

from agents.deps import Deps
from agents.judge_agent import judge
from ports.search_client import SearchClient
from tools import get_today
from tools.web import web_read, web_search

load_dotenv()  # load COMPANY_MODEL / LLM_MODEL / keys from .env

COMPANY_MODEL = os.environ.get("COMPANY_MODEL", os.environ.get("LLM_MODEL", "openai:gpt-5-mini"))


class SegmentFinding(BaseModel):
    """One revenue segment of a company (agent output)."""

    segment: str                             # e.g. "Cloud", "iPhone", "Services"
    percentage: float = Field(ge=0, le=100)  # % of company revenue, source-backed
    source: str = ""                         # source URL / short note


class PortfolioResult(BaseModel):
    """A company's revenue breakdown by segment (the segments sum to ~100)."""

    segments: list[SegmentFinding] = Field(default_factory=list)


company_agent = Agent(
    COMPANY_MODEL,
    deps_type=Deps,
    output_type=PortfolioResult,
    retries=2,
    system_prompt=(
        "FIRST call `get_today` to anchor on today's date; search for CURRENT data, NOT your "
        "training-cutoff year. You research ONE company's revenue breakdown by business segment "
        "(e.g. Cloud, Advertising, Devices). Use `web_search` to FIND the latest segment "
        "breakdown (the company's 10-K / annual report / IR page or other reputable sources), "
        "then use `web_read` on the best URL to READ that page and pull the real figures. "
        "Express each segment as a percentage (0-100) in `segments`. The percentages MUST sum "
        "to ~100; if the named segments don't cover everything, add an 'Others' segment. Use "
        "ONLY source-backed figures -- NEVER invent percentages. If no reliable breakdown is "
        "available, return EMPTY `segments` (better empty than fabricated). IMPORTANT: spend at "
        "most 2-3 searches -- if you can't find a source-backed split quickly, STOP and return "
        "empty. Put each segment's source URL in `source`."
    ),
)


# Shared agent tools (tools/): get_today anchors on the date; web_search/web_read delegate
# to the SearchClient adapter in Deps.
company_agent.tool_plain(get_today)
company_agent.tool(web_search)
company_agent.tool(web_read)


# Portfolio rubric -- domain criteria for the generic judge (sums/sourcing are checkable).
PORTFOLIO_RUBRIC = (
    "1) Each `segments` entry has a label and a percentage attributable to a reputable source "
    "(company 10-K/IR, Reuters, Bloomberg, etc.). Judge by source-domain reputation only.\n"
    "2) The percentages sum to roughly 100 (an 'Others' slice is fine). No invented numbers -- "
    "if the breakdown is not sourced, `segments` should be empty instead.\n"
    "3) A `source` is present on each segment whenever `segments` is non-empty.\n"
    "4) The data is RECENT -- today is {today}. Years-old figures (e.g. 2023 numbers when today "
    "is 2026) are STALE: FAIL them and require current-period data."
)


@company_agent.output_validator
def check_format(data: PortfolioResult) -> PortfolioResult:
    """Layer 1 -- deterministic checks on the percentages (pure compute -> sync)."""
    problems: list[str] = []
    for seg in data.segments:
        if not 0.0 <= seg.percentage <= 100.0:
            problems.append(f"Segment '{seg.segment}' percentage must be between 0 and 100.")
    if data.segments:
        total = sum(seg.percentage for seg in data.segments)
        if not 90.0 <= total <= 110.0:
            problems.append(
                f"Portfolio percentages sum to {total:.0f}; they must total ~100 "
                "(add an 'Others' segment for the remainder)."
            )
        if not any(seg.source for seg in data.segments):
            problems.append("Provide at least one source for a non-empty portfolio.")
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@company_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: PortfolioResult) -> PortfolioResult:
    """Layer 2 -- pass the portfolio rubric to the generic judge (skip when empty)."""
    if not data.segments:
        return data  # an empty portfolio is acceptable (no source-backed breakdown found)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    verdict = await judge(PORTFOLIO_RUBRIC.format(today=today), data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry(
            "Your previous portfolio was largely correct. Keep everything else identical "
            "and fix ONLY these specific issues:\n- " + "\n- ".join(verdict.issues)
        )
    return data


async def research_portfolio(
    name: str, *, search: SearchClient, usage: RunUsage | None = None
) -> PortfolioResult:
    """Research one company's revenue breakdown by segment -> PortfolioResult."""
    result = await company_agent.run(
        f"Research the revenue breakdown by segment of {name}.",
        deps=Deps(search=search),
        usage=usage,
    )
    return result.output


async def main() -> None:
    from adapters.serper.search_client import SerperClient

    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        result = await research_portfolio("Microsoft", search=search)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
