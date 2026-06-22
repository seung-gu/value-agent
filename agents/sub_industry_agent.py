"""Industry agent -- researches ONE sub-industry's market share by company.

Given a sub-industry name (e.g. 'Cloud Infrastructure'), it uses web search to find the
company market shares in that market and returns them as source-backed percentages. The
orchestrator fans out to this agent for the sector's ~5 sub-industries in parallel.

Two-layer validation (both @output_validator -> ModelRetry, retries=2):
- Layer 1 (format): shares 0-100 and sum to ~100 (deterministic, free).
- Layer 2 (quality): pass the market-share rubric to the generic judge.

Run:
    uv run python -m agents.sub_industry_agent
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.usage import RunUsage

from agents.judge_agent import judge
from domain import SubIndustry
from ports.search_client import SearchClient
from tools import get_today
from agents.deps import Deps
from tools.web import web_read, web_search

load_dotenv()  # load INDUSTRY_MODEL / LLM_MODEL / keys from .env

# Default to the same model as the sector agent; overridable via INDUSTRY_MODEL.
INDUSTRY_MODEL = os.environ.get("INDUSTRY_MODEL", os.environ.get("LLM_MODEL", "openai:gpt-5-mini"))


sub_industry_agent = Agent(
    INDUSTRY_MODEL,
    deps_type=Deps,
    output_type=SubIndustry,
    retries=2,
    system_prompt=(
        "You research ONE sub-industry / market and find the COMPANY MARKET SHARES in it. "
        "FIRST call `get_today` to anchor on today's date, and search for CURRENT data -- "
        "NOT your training-cutoff year (do not put 2023/2024 in queries unless asked).\n"
        "WORKFLOW: use `web_search` to FIND a market-share report (IDC, Gartner, Synergy, "
        "Counterpoint, TrendForce, Statista, company filings), then use `web_read` on the "
        "best URL to READ that page -- search snippets do NOT contain the share table, so "
        "READ the actual page and pull the numbers from it. One or two good reads beat many "
        "searches. Put each leading company's share as a percentage (0-100) in `companies`. "
        "The shares MUST sum to ~100; add an 'Others' company for the remainder. Use ONLY "
        "source-backed figures from the page you read -- NEVER invent shares. If no reliable "
        "share data exists, return an EMPTY `companies` list (better empty than fabricated). "
        "For each company, put a short source-backed note in its `evidence`. Always record "
        "the source URLs. Keep `name` exactly as given."
    ),
)


# Shared agent tools (tools/): get_today anchors on the date; web_search/web_read delegate
# to the SearchClient adapter in Deps.
sub_industry_agent.tool_plain(get_today)
sub_industry_agent.tool(web_search)
sub_industry_agent.tool(web_read)


# Market-share rubric -- domain criteria for the generic judge (sums/sourcing are checkable).
SHARE_RUBRIC = (
    "1) Each company in `companies` has a share attributable to a reputable market-research "
    "or filings source (IDC, Gartner, Synergy, Statista, SEC, etc.). Judge by source-domain "
    "reputation only; do not verify exact URLs.\n"
    "2) The shares sum to roughly 100 (an 'Others' entry is fine). No invented numbers -- if "
    "share data is not sourced, `companies` should be empty instead.\n"
    "3) `sources` are present whenever `companies` is non-empty.\n"
    "4) The data is RECENT -- today is {today}. Years-old shares (e.g. 2023 figures when "
    "today is 2026) are STALE: FAIL them and require current-period data."
)


@sub_industry_agent.output_validator
def check_format(data: SubIndustry) -> SubIndustry:
    """Layer 1 -- deterministic checks on the shares (pure compute -> sync)."""
    problems: list[str] = []
    for c in data.companies:
        if not 0.0 <= c.share <= 100.0:
            problems.append(f"Company '{c.company}' share must be between 0 and 100.")
    if data.companies:
        total = sum(c.share for c in data.companies)
        if not 90.0 <= total <= 110.0:
            problems.append(
                f"Company shares sum to {total:.0f}; they must total ~100 "
                "(add an 'Others' company for the remainder)."
            )
        if not data.sources:
            problems.append("Provide at least one source URL for non-empty companies.")
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@sub_industry_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: SubIndustry) -> SubIndustry:
    """Layer 2 -- pass the market-share rubric to the generic judge (skip when empty)."""
    if not data.companies:
        return data  # an empty share list is acceptable (no source-backed data found)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    verdict = await judge(SHARE_RUBRIC.format(today=today), data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry(
            "Your previous shares were largely correct. Keep everything else identical "
            "and fix ONLY these specific issues:\n- " + "\n- ".join(verdict.issues)
        )
    return data


async def research_sub_industry(
    name: str, *, search: SearchClient, usage: RunUsage | None = None
) -> SubIndustry:
    """Research one sub-industry's company market shares -> SubIndustry (weight left 0).

    usage: pass the caller's RunUsage to aggregate token usage across the fan-out.
    """
    result = await sub_industry_agent.run(
        f"Research the company market shares in the '{name}' market.",
        deps=Deps(search=search),
        usage=usage,
    )
    return result.output


async def main() -> None:
    from adapters.serper.search_client import SerperClient

    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        result = await research_sub_industry("Cloud Infrastructure", search=search)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
