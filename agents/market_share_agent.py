"""Market-share agent -- researches ONE sub-industry's company market shares.

Given a sub-industry name (e.g. 'Foundry'), it finds each leading company's % share
(source-backed) and returns them as `ShareFinding`s. It deals in company NAMES + percentages
only; the orchestrator turns these into normalized `market_share` rows (assigning the
surrogate company_code / sub_code / period). The orchestrator fans out to this agent per
sub-industry in parallel.

Two-layer validation (both @output_validator -> ModelRetry, retries=2):
- Layer 1 (format): shares 0-100 and sum to ~100 (deterministic, free).
- Layer 2 (quality): pass the market-share rubric to the generic judge.

Run:
    uv run python -m agents.market_share_agent
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

load_dotenv()  # load MARKET_SHARE_MODEL / LLM_MODEL / keys from .env

MARKET_SHARE_MODEL = os.environ.get(
    "MARKET_SHARE_MODEL", os.environ.get("LLM_MODEL", "openai:gpt-5-mini")
)


class ShareFinding(BaseModel):
    """One company's market share within a sub-industry (agent output -- name, not code)."""

    company: str                             # company name as found
    percentage: float = Field(ge=0, le=100)  # % within the sub-industry, source-backed
    source: str = ""                         # source URL / short note


class MarketShareResult(BaseModel):
    """The market-share split found for one sub-industry (the shares sum to ~100)."""

    shares: list[ShareFinding] = Field(default_factory=list)


market_share_agent = Agent(
    MARKET_SHARE_MODEL,
    deps_type=Deps,
    output_type=MarketShareResult,
    retries=2,
    system_prompt=(
        "You research ONE sub-industry / market and find the COMPANY MARKET SHARES in it. "
        "FIRST call `get_today` to anchor on today's date, and search for CURRENT data -- "
        "NOT your training-cutoff year (do not put 2023/2024 in queries unless asked).\n"
        "WORKFLOW: use `web_search` to FIND a market-share report (IDC, Gartner, Synergy, "
        "Counterpoint, TrendForce, Statista, company filings), then use `web_read` on the "
        "best URL to READ that page -- search snippets do NOT contain the share table, so "
        "READ the actual page and pull the numbers from it. One or two good reads beat many "
        "searches. Put each leading company's share as a percentage (0-100) in `shares`. "
        "The shares MUST sum to ~100; add an 'Others' company for the remainder. Use ONLY "
        "source-backed figures from the page you read -- NEVER invent shares. If no reliable "
        "share data exists, return an EMPTY `shares` list (better empty than fabricated). "
        "For each company, put its source URL/note in `source`."
    ),
)


# Shared agent tools (tools/): get_today anchors on the date; web_search/web_read delegate
# to the SearchClient adapter in Deps.
market_share_agent.tool_plain(get_today)
market_share_agent.tool(web_search)
market_share_agent.tool(web_read)


# Market-share rubric -- domain criteria for the generic judge (sums/sourcing are checkable).
SHARE_RUBRIC = (
    "1) Each company in `shares` has a percentage attributable to a reputable market-research "
    "or filings source (IDC, Gartner, Synergy, Statista, SEC, etc.). Judge by source-domain "
    "reputation only; do not verify exact URLs.\n"
    "2) The shares sum to roughly 100 (an 'Others' entry is fine). No invented numbers -- if "
    "share data is not sourced, `shares` should be empty instead.\n"
    "3) A `source` is present on each company whenever `shares` is non-empty.\n"
    "4) The data is RECENT -- today is {today}. Years-old shares (e.g. 2023 figures when "
    "today is 2026) are STALE: FAIL them and require current-period data."
)


@market_share_agent.output_validator
def check_format(data: MarketShareResult) -> MarketShareResult:
    """Layer 1 -- deterministic checks on the shares (pure compute -> sync)."""
    problems: list[str] = []
    for s in data.shares:
        if not 0.0 <= s.percentage <= 100.0:
            problems.append(f"Company '{s.company}' share must be between 0 and 100.")
    if data.shares:
        total = sum(s.percentage for s in data.shares)
        if not 90.0 <= total <= 110.0:
            problems.append(
                f"Company shares sum to {total:.0f}; they must total ~100 "
                "(add an 'Others' company for the remainder)."
            )
        if not any(s.source for s in data.shares):
            problems.append("Provide at least one source for non-empty shares.")
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@market_share_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: MarketShareResult) -> MarketShareResult:
    """Layer 2 -- pass the market-share rubric to the generic judge (skip when empty)."""
    if not data.shares:
        return data  # an empty share list is acceptable (no source-backed data found)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    verdict = await judge(SHARE_RUBRIC.format(today=today), data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry(
            "Your previous shares were largely correct. Keep everything else identical "
            "and fix ONLY these specific issues:\n- " + "\n- ".join(verdict.issues)
        )
    return data


async def research_market_share(
    name: str, *, search: SearchClient, usage: RunUsage | None = None
) -> MarketShareResult:
    """Research one sub-industry's company market shares -> MarketShareResult.

    usage: pass the caller's RunUsage to aggregate token usage across the fan-out.
    """
    result = await market_share_agent.run(
        f"Research the company market shares in the '{name}' market.",
        deps=Deps(search=search),
        usage=usage,
    )
    return result.output


async def main() -> None:
    from adapters.serper.search_client import SerperClient

    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        result = await research_market_share("Foundry", search=search)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
