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
from agents.source_guard import read_urls, unread_sources
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
    as_of: str = ""  # fiscal period the figures are FROM, read off the filing (e.g. "FY2025")


company_agent = Agent(
    COMPANY_MODEL,
    deps_type=Deps,
    output_type=PortfolioResult,
    retries=4,
    system_prompt=(
        "You research ONE company's revenue breakdown by business segment (e.g. Cloud, "
        "Advertising, Devices).\n"
        "FIRST call `get_today` to anchor on today's date, then search for the MOST RECENT "
        "breakdown available -- not your training-cutoff year.\n"
        "WORKFLOW:\n"
        "1) `web_search` to find the latest segment breakdown (the company's 10-K / annual "
        "report / 10-Q / IR deck, or reputable coverage of it).\n"
        "2) `web_read` the most promising result -- snippets rarely hold the segment table, "
        "so READ the page and pull the real figures from it.\n"
        "3) If that source is thin, incomplete, or stale, DO NOT settle: search again with "
        "different terms (the fiscal year, 'segment revenue', '10-K') and `web_read` another "
        "page.\n"
        "COVERAGE: include every reported segment. Express each as a percentage (0-100) in "
        "`segments`; they MUST sum to ~100 -- put any remainder in a single 'Others' segment.\n"
        "RECENCY: set `as_of` to the fiscal period the figures come FROM (read it off the "
        "filing), NOT today's date. Write it as a BARE period only -- exactly 'FY2024' or "
        "'YYYY-Qn' (e.g. 'FY2025' or '2025-Q3'), with no extra words.\n"
        "HONESTY: use ONLY source-backed figures. Each `source` MUST be the URL of a page "
        "you ACTUALLY opened with web_read -- never cite an upstream link you only saw in "
        "search results (it may be dead/paywalled), and never invent percentages. If the "
        "page you read attributes the data to someone else, still cite the page you read. "
        "Return an EMPTY `segments` list ONLY as a last resort, after genuinely trying "
        "several sources and finding no reliable breakdown."
    ),
)


# Shared agent tools (tools/): get_today anchors on the date; web_search/web_read delegate
# to the SearchClient adapter in Deps.
company_agent.tool_plain(get_today)
company_agent.tool(web_search)
company_agent.tool(web_read)


# Portfolio rubric -- domain criteria for the generic judge (sums/sourcing are checkable).
PORTFOLIO_RUBRIC = (
    "1) SOURCING: each `segments` entry has a label and a percentage attributable to a "
    "reputable source (company 10-K/10-Q/IR, Reuters, Bloomberg, etc.). Judge by "
    "source-domain reputation only.\n"
    "2) SUM: the percentages sum to roughly 100 (an 'Others' slice is fine). No invented "
    "numbers -- if the breakdown is not sourced, `segments` should be empty instead.\n"
    "3) SOURCE PRESENT: a `source` is present on each segment whenever `segments` is "
    "non-empty.\n"
    "4) COVERAGE: the company's main reported segments are present. If a segment you are "
    "confident is material is clearly missing, FAIL and name it. If you are not sure, do not "
    "flag it.\n"
    "5) RECENCY: `as_of` names the fiscal period and is recent -- today is {today}. Figures "
    "more than ~1 year old (e.g. 2023 numbers when today is 2026) are STALE: FAIL them and "
    "require current-period data."
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
        if not data.as_of.strip():
            problems.append(
                "Set `as_of` to the fiscal period the figures are from (read from the "
                "filing, e.g. 'FY2025')."
            )
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@company_agent.output_validator
def check_sources_read(ctx: RunContext[Deps], data: PortfolioResult) -> PortfolioResult:
    """Layer 1.5 -- every source must EXACTLY match a page actually web_read (no laundering)."""
    if not data.segments:
        return data
    read = read_urls(ctx.messages)
    if not read:
        return data  # nothing read yet; let the other layers handle it
    bad = unread_sources([s.source for s in data.segments], read)
    if bad:
        raise ModelRetry(
            "Every `source` must EXACTLY match (same host+path) a page you opened with "
            "web_read. These do NOT -- you never opened them:\n- "
            + "\n- ".join(sorted(set(bad)))
            + "\nUse one of the EXACT URLs you actually read:\n- "
            + "\n- ".join(read)
        )
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
            "Your portfolio is close but not done. Fix these -- and if an issue is about a "
            "missing segment or stale/insufficient data, run MORE searches and `web_read` "
            "another source to fill it in (don't just resubmit the same list):\n- "
            + "\n- ".join(verdict.issues)
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
