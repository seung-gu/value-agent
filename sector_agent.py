"""
Sector analysis agent (top-down entry point).

Takes one US GICS sector, researches its growth/potential via web search (Serper),
and returns a SectorAnalysis. Also discovers competitive companies.

Two-layer validation (both run in @output_validator -> on failure ModelRetry makes
PydanticAI re-analyze automatically):
- Layer 1 (format/evidence): deterministic checks (source count, companies, required fields).
- Layer 2 (subjective quality): pass the sector rubric to the generic judge_agent to
  assess source reputation / recency.

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

from judge_agent import judge
from models import SectorAnalysis
from search import SearchClient, SerperClient

load_dotenv()  # load LLM_MODEL, ANTHROPIC_API_KEY, SERPER_API_KEY from .env

# Observability: trace agent / LLM / tool calls with logfire.
# send_to_logfire="if-token-present" -> local-only without a token, cloud with one.
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()  # instrument every agent run
logfire.instrument_httpx()         # also trace Serper calls


# ---------------------------------------------------------------------------
# Dependencies (deps) -- injected at runtime
# ---------------------------------------------------------------------------
@dataclass
class Deps:
    search: SearchClient  # search client holding the key + http (e.g. Serper)


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------
sector_agent = Agent(
    os.environ.get("LLM_MODEL", "anthropic:claude-sonnet-4-6"),
    deps_type=Deps,
    output_type=SectorAnalysis,
    retries=2,  # output_validator(ModelRetry) budget -- retry on format/quality failure
    system_prompt=(
        "You are an equity sector analyst. "
        "FIRST, always call the `get_today` tool to anchor on today's real date "
        "and current quarter. Prioritize the MOST RECENT data, and restrict your "
        "research to the window it reports (current quarter ±4 quarters). "
        "Then, given a US GICS sector, use the `web_search` tool to research its "
        "growth potential: market size, CAGR, key growth drivers, and the most "
        "competitive companies in it. When you search, include the relevant year/"
        "quarter (e.g. '2026', 'Q2 2026') so results stay current. "
        "Gather additional useful metrics beyond CAGR/market size when relevant. "
        "Always record the source URLs you relied on. Be conservative with "
        "potential_score and confidence when data is thin."
    ),
)


@sector_agent.tool_plain
def get_today() -> str:
    """Return today's date, current quarter, and the recommended research window (current quarter +/-4Q).

    Gives the agent the real current date so it anchors "most recent" data correctly.
    Registered as tool_plain (context-free) since it needs no deps.
    """
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
    """Web search (cleaned results). Used to research sector market size / CAGR / competitors.

    The actual search + cleaning is handled by the deps SearchClient (Serper etc, swappable).
    """
    return await ctx.deps.search.search(query)


# ---------------------------------------------------------------------------
# Output validation -- two @output_validators. They run in definition order and both
# must pass. On failure, raising ModelRetry feeds it back to the model and PydanticAI
# re-analyzes automatically (retries=2). check_format runs first, so a format failure
# skips the expensive judge (LLM) call.
# Ref: https://ai.pydantic.dev/output/  (output validators & ModelRetry)
# ---------------------------------------------------------------------------
@sector_agent.output_validator
def check_format(data: SectorAnalysis) -> SectorAnalysis:
    """Layer 1 -- deterministic format/evidence checks (pure compute -> sync)."""
    problems: list[str] = []
    if len(data.sources) < 3:
        problems.append("Provide at least 3 source URLs.")
    if not data.top_companies:
        problems.append("List at least one competitive company.")
    if not data.market_size.strip() or not data.cagr.strip():
        problems.append("market_size and cagr must not be empty.")
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


# Sector-specific rubric -- domain criteria are owned by the caller (judge_agent is generic).
# Written as measurable criteria checkable from the output (industry best practice).
SECTOR_RUBRIC = (
    "1) The `sources` list contains URLs from reputable DOMAINS (e.g. gartner.com, "
    "reuters.com, bloomberg.com, sec.gov, *.gov, or established research/news/company-IR "
    "sites). Judge by domain reputation only — do not try to verify the exact URL.\n"
    "2) market_size and cagr each include a figure with a year/period, and "
    "top_companies and key_drivers are non-empty.\n"
    "3) The figures reference recent periods. Today is {today}; treat dates on or "
    "before today as valid current/past data (do NOT treat current-year dates as "
    "future or fabricated). Flag only data that is obviously years-old stale. "
    "Trust cited reputable sources for the values."
)


@sector_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: SectorAnalysis) -> SectorAnalysis:
    """Layer 2 -- pass the sector rubric to the generic judge for subjective quality (LLM call -> async)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # fill today's date into the rubric
    verdict = await judge(SECTOR_RUBRIC.format(today=today), data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry("Improve quality:\n- " + "\n- ".join(verdict.issues))
    return data


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
async def main() -> None:
    async with httpx.AsyncClient() as client:
        deps = Deps(search=SerperClient(os.environ["SERPER_API_KEY"], client))
        result = await sector_agent.run(
            "Analyze the Information Technology sector.", deps=deps
        )
        print(result.output)
        print(result.usage)


if __name__ == "__main__":
    asyncio.run(main())
