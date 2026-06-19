"""Sector agent (stage 1 of the orchestration).

Given a US GICS sector, it identifies the sector's top ~5 SUB-INDUSTRIES and each one's
weight (share of the sector), plus high-level sector metrics. It does NOT fill company
shares or portfolios -- the orchestrator fans out to industry_agent (per-sub-industry
shares) and company_agent (per-company portfolio) afterwards.

Two-layer validation (@output_validator -> ModelRetry, retries=2):
- Layer 1 (format): ~3-7 sub-industries, weights sum ~100, metrics present.
- Layer 2 (quality): sub-industry rubric via the generic judge.

Run:
    uv run sector_agent.py
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import logfire
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.usage import RunUsage

from judge_agent import judge
from models import SectorAnalysis
from search import SearchClient, SerperClient

load_dotenv()  # load LLM_MODEL / keys from .env

# Observability: trace agent / LLM / tool calls with logfire (configured once here;
# the other agents are instrumented globally by this same call).
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()
logfire.instrument_httpx()


@dataclass
class Deps:
    search: SearchClient  # search client holding the key + http (e.g. Serper)


sector_agent = Agent(
    os.environ.get("LLM_MODEL", "openai:gpt-5-mini"),
    deps_type=Deps,
    output_type=SectorAnalysis,
    retries=2,
    system_prompt=(
        "You are an equity sector analyst. FIRST call the `get_today` tool to anchor on "
        "today's real date and quarter; prioritize the MOST RECENT data. Then, given a US "
        "GICS sector, use `web_search` to research MARKET-RESEARCH / INDUSTRY reports (IDC, "
        "Gartner, Statista, Grand View Research, Mordor, Precedence, etc.) and do TWO things:\n"
        "1) Fill the sector-level metrics: market_size and cagr (each a figure with a "
        "year/period), key_drivers (what is driving the growth), and a conservative "
        "potential_score/confidence.\n"
        "2) Identify the MAJOR SUB-INDUSTRIES the same reports highlight for this sector -- "
        "especially the ones DRIVING the growth (e.g. for Information Technology: Cloud "
        "Infrastructure, Semiconductors, AI/Software...). For each, set `name` and, ONLY if "
        "a report gives it, `market_size` (otherwise leave it empty -- do NOT force a "
        "number). Do NOT compute artificial sector weights, and do NOT use ETF factsheets "
        "-- use market-research / industry sources.\n"
        "Leave every sub-industry's `companies` EMPTY and leave `company_portfolios` EMPTY "
        "-- later stages fill those. Keep searches FOCUSED: a single market report covering "
        "the sector usually lists its growth AND its sub-industries together, so a few "
        "searches is plenty. Always record the source URLs you relied on."
    ),
)


@sector_agent.tool_plain
def get_today() -> str:
    """Return today's date, current quarter, and the recommended research window (current quarter +/-4Q)."""
    now = datetime.now(timezone.utc)
    q = (now.month - 1) // 3 + 1

    def shift_quarter(year: int, quarter: int, delta: int) -> tuple[int, int]:
        idx = year * 4 + (quarter - 1) + delta
        return idx // 4, idx % 4 + 1

    start_y, start_q = shift_quarter(now.year, q, -4)
    end_y, end_q = shift_quarter(now.year, q, 4)
    return (
        f"Today is {now:%Y-%m-%d}, which is Q{q} {now.year}. "
        f"Prioritize the most recent data. "
        f"Research window: Q{start_q} {start_y} to Q{end_q} {end_y} "
        f"(current quarter +/-4 quarters)."
    )


@sector_agent.tool
async def web_search(ctx: RunContext[Deps], query: str) -> str:
    """Web search (cleaned results). Used to research sub-industries / weights / metrics."""
    return await ctx.deps.search.search(query)


# Sector-specific rubric for the generic judge.
SECTOR_RUBRIC = (
    "1) `sub_industries` lists ~3-7 real, MAJOR sub-industries that reputable market-research "
    "sources actually highlight for this sector (each with a name; market_size optional). "
    "They must be industry sub-segments, NOT ETF holdings. Judge by source-domain reputation "
    "only.\n"
    "2) market_size and cagr each include a figure with a year/period, and key_drivers is "
    "non-empty.\n"
    "3) Figures reference recent periods. Today is {today}; treat dates on or before today "
    "as valid current/past data (do NOT treat current-year dates as future/fabricated). "
    "Trust cited reputable sources for the values."
)


@sector_agent.output_validator
def check_format(data: SectorAnalysis) -> SectorAnalysis:
    """Layer 1 -- deterministic format checks (pure compute -> sync)."""
    problems: list[str] = []
    if not 3 <= len(data.sub_industries) <= 7:
        problems.append("Identify between 3 and 7 major sub-industries.")
    if not data.market_size.strip() or not data.cagr.strip():
        problems.append("market_size and cagr must not be empty.")
    if len(data.sources) < 2:
        problems.append("Provide at least 2 source URLs.")
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@sector_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: SectorAnalysis) -> SectorAnalysis:
    """Layer 2 -- pass the sector rubric to the generic judge (LLM call -> async)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    verdict = await judge(SECTOR_RUBRIC.format(today=today), data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry(
            "Your previous analysis was largely correct. Keep everything else identical "
            "and fix ONLY these specific issues:\n- " + "\n- ".join(verdict.issues)
        )
    return data


async def identify_sub_industries(
    sector: str, *, search: SearchClient, usage: RunUsage | None = None
) -> SectorAnalysis:
    """Stage 1 -- identify the sector's sub-industries + weights + metrics (companies left empty)."""
    result = await sector_agent.run(
        f"Analyze the {sector} sector.", deps=Deps(search=search), usage=usage
    )
    return result.output


async def main() -> None:
    async with httpx.AsyncClient() as client:
        deps = Deps(search=SerperClient(os.environ["SERPER_API_KEY"], client))
        result = await sector_agent.run("Analyze the Information Technology sector.", deps=deps)
        print(result.output)


if __name__ == "__main__":
    asyncio.run(main())
