"""Company agent -- researches ONE company's business portfolio (for the portfolio pie).

Given a company name (one of the sector's top companies), it uses web search to find
how the company's revenue breaks down by segment (e.g. Cloud, Advertising, Devices)
and returns source-backed percentages. The orchestrator fans out to this agent for the
sector's top-5 companies in parallel.

Two-layer validation (both @output_validator -> ModelRetry on failure, retries=2):
- Layer 1 (format): percentages in 0-100 and sum to ~100 (deterministic, free).
- Layer 2 (quality): pass the portfolio rubric to the generic judge.

Run:
    uv run company_agent.py
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.usage import RunUsage

from judge_agent import judge
from models import CompanyPortfolio
from search import SearchClient, SerperClient

load_dotenv()  # load COMPANY_MODEL / LLM_MODEL / keys from .env

# Default to the same model as the sector agent; overridable via COMPANY_MODEL.
COMPANY_MODEL = os.environ.get("COMPANY_MODEL", os.environ.get("LLM_MODEL", "openai:gpt-5-mini"))


@dataclass
class Deps:
    search: SearchClient  # search client holding the key + http (e.g. Serper)


company_agent = Agent(
    COMPANY_MODEL,
    deps_type=Deps,
    output_type=CompanyPortfolio,
    retries=2,
    system_prompt=(
        "You research ONE company's business portfolio: how its revenue breaks down by "
        "business segment (e.g. Cloud, Advertising, Devices). Use the `web_search` tool "
        "to find the latest segment breakdown from the company's 10-K / annual report / "
        "IR page or other reputable sources. Express each segment as a percentage (0-100). "
        "The percentages MUST sum to ~100; if the named segments don't cover everything, "
        "add an 'Others' segment for the remainder. Use ONLY source-backed figures -- "
        "NEVER invent or estimate percentages. If no reliable breakdown is available, "
        "return an EMPTY portfolio (better empty than fabricated). IMPORTANT: spend at "
        "most 2-3 searches -- segment breakdowns are often unavailable, so if you can't "
        "find a source-backed split quickly, STOP and return empty rather than searching "
        "repeatedly. Always record the source URLs you relied on."
    ),
)


@company_agent.tool
async def web_search(ctx: RunContext[Deps], query: str) -> str:
    """Web search (cleaned results) to research the company's segment revenue breakdown."""
    return await ctx.deps.search.search(query)


# Portfolio rubric -- domain criteria for the generic judge (sums/sourcing are checkable).
PORTFOLIO_RUBRIC = (
    "1) Each `portfolio` segment has a label and a percentage attributable to a reputable "
    "source (company 10-K/IR, Reuters, Bloomberg, etc.). Judge by source-domain reputation "
    "only; do not verify exact URLs.\n"
    "2) The segment percentages sum to roughly 100 (an 'Others' slice is fine). No invented "
    "numbers -- if the breakdown is not sourced, the portfolio should be empty instead.\n"
    "3) `sources` are present whenever the portfolio is non-empty."
)


@company_agent.output_validator
def check_format(data: CompanyPortfolio) -> CompanyPortfolio:
    """Layer 1 -- deterministic checks on the percentages (pure compute -> sync)."""
    problems: list[str] = []
    for seg in data.portfolio:
        if not 0.0 <= seg.percentage <= 100.0:
            problems.append(f"Segment '{seg.label}' percentage must be between 0 and 100.")
    if data.portfolio:
        total = sum(seg.percentage for seg in data.portfolio)
        if not 90.0 <= total <= 110.0:
            problems.append(
                f"Portfolio percentages sum to {total:.0f}; they must total ~100 "
                "(add an 'Others' segment for the remainder)."
            )
        if not data.sources:
            problems.append("Provide at least one source URL for a non-empty portfolio.")
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@company_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: CompanyPortfolio) -> CompanyPortfolio:
    """Layer 2 -- pass the portfolio rubric to the generic judge (skip when empty)."""
    if not data.portfolio:
        return data  # an empty portfolio is acceptable (no source-backed breakdown found)
    verdict = await judge(PORTFOLIO_RUBRIC, data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry(
            "Your previous portfolio was largely correct. Keep everything else identical "
            "and fix ONLY these specific issues:\n- " + "\n- ".join(verdict.issues)
        )
    return data


async def research_company(
    name: str, *, search: SearchClient, usage: RunUsage | None = None
) -> CompanyPortfolio:
    """Research one company's business portfolio -> CompanyPortfolio.

    usage: pass the caller's RunUsage to aggregate token usage across the fan-out.
    """
    result = await company_agent.run(
        f"Research the business portfolio of {name}.",
        deps=Deps(search=search),
        usage=usage,
    )
    return result.output


async def main() -> None:
    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        result = await research_company("Microsoft", search=search)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
