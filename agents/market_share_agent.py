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
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage

from agents.deps import Deps
from agents.judge_agent import judge
from agents.research import research_agent
from agents.source_guard import read_urls, unread_sources
from ports.search_client import SearchClient

load_dotenv()  # load MARKET_SHARE_MODEL / LLM_MODEL / keys from .env

MARKET_SHARE_MODEL = os.environ.get(
    "MARKET_SHARE_MODEL", os.environ.get("LLM_MODEL", "openai:gpt-5-mini")
)


class ShareFinding(BaseModel):
    """One company's market share within a sub-industry (agent output -- name, not code)."""

    company: str                             # company name as found
    ticker: str = ""                         # US exchange ticker if listed (e.g. "NVDA"), else ""
    percentage: float = Field(ge=0, le=100)  # % within the sub-industry, source-backed
    source: str = ""                         # source URL / short note


class MarketShareResult(BaseModel):
    """The market-share split found for one sub-industry (the shares sum to ~100)."""

    shares: list[ShareFinding] = Field(default_factory=list)
    as_of: str = ""  # reporting period the shares are FROM, read off the source (e.g. "2024")


market_share_agent = research_agent(
    MARKET_SHARE_MODEL,
    MarketShareResult,
    retries=4,
    instructions=(
        "You research ONE sub-industry / market and find the COMPANY MARKET SHARES in it.\n"
        "FIRST call `get_today` to anchor on today's date, then search for the MOST RECENT "
        "data available -- not your training-cutoff year.\n"
        "SEARCH DISCIPLINE (this is where agents fail -- follow it strictly):\n"
        "1) Keep each `web_search` SHORT: at most ~6 plain words, NO quote marks, and never "
        "paste a guessed page title. Start broad, then narrow. "
        "Bad: '\"X market share\" \"by vendor\" 2024 \"Gartner\" \"percent\"'. "
        "Good: 'X market share by vendor 2025'.\n"
        "2) Read the answer from the search SNIPPETS first -- shares/figures are often right "
        "there. Only `web_read` when a snippet shows an OPEN page actually has the table.\n"
        "3) Prefer OPEN primary sources (EIA, OPEC, the Energy Institute review, company "
        "filings, government stats). Gated aggregators (Statista, Rystad, Wood Mackenzie, "
        "Gartner, IDC) are paywalled -- don't try to read them.\n"
        "4) On a miss, switch ANGLE or SOURCE -- never reword the same query. If a few angles "
        "all come up dry, STOP and return EMPTY shares: do NOT estimate or pad. No public "
        "table = empty.\n"
        "COVERAGE: include every vendor with a non-trivial share from what you found (not "
        "just the top 2-3); put the remainder in a single 'Others' entry so it sums to ~100.\n"
        "TICKER: for each company set `ticker` to its primary US stock-exchange ticker (e.g. "
        "'NVDA', 'AVGO', or a US-listed ADR like 'TSM') when it is US-listed; leave it EMPTY "
        "for private, foreign-only, or aggregate ('Others') entries. It's used to pull SEC "
        "financials, so give the REAL exchange ticker -- empty is better than a wrong guess.\n"
        "RECENCY: set `as_of` to the reporting period the figures come FROM (read it off the "
        "source), NOT today's date. Write it as a BARE period only -- exactly 'YYYY' or "
        "'YYYY-Qn' (e.g. '2024' or '2025-Q4'), with no extra words. Always prefer the most "
        "recent report you can find.\n"
        "HONESTY: use ONLY source-backed figures. Each `source` MUST be the URL of a page "
        "you ACTUALLY opened with web_read -- never cite an upstream link you only saw in "
        "search results (it may be dead/paywalled), and never invent shares. If the page you "
        "read attributes the data to someone else, still cite the page you read. Return an "
        "EMPTY `shares` list ONLY as a last resort, after genuinely trying several searches "
        "and sources and finding no reputable data -- empty is not a quick way out."
    ),
)


# Market-share rubric -- domain criteria for the generic judge (sums/sourcing are checkable).
SHARE_RUBRIC = (
    "1) SOURCING: each company's share is attributable to a reputable market-research or "
    "filings source (IDC, Gartner, Synergy, Counterpoint, TrendForce, Canalys, Omdia, "
    "Statista, SEC, etc.). Judge by source-domain reputation only; do not verify exact URLs.\n"
    "2) SUM: the shares sum to roughly 100 (an 'Others' entry is fine). No invented numbers "
    "-- if share data is not sourced, `shares` should be empty instead.\n"
    "3) SOURCE PRESENT: a `source` is present on each company whenever `shares` is non-empty.\n"
    "4) COVERAGE: the obvious market leaders are present. If a company you are confident is a "
    "major player in THIS specific market is clearly missing, FAIL and name it so it can be "
    "added. If you are not sure it belongs, do not flag it.\n"
    "5) RECENCY: `as_of` names the reporting period and is recent -- today is {today}. Data "
    "older than ~2 years (e.g. 2023 or earlier when today is 2026) is STALE: FAIL it and "
    "require current data.\n"
    "6) PLAUSIBILITY: the distribution is internally sensible (e.g. not a single small named "
    "leader sitting next to a huge unexplained 'Others')."
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
        if not data.as_of.strip():
            problems.append(
                "Set `as_of` to the reporting period the shares are from (read from the "
                "source, e.g. '2024')."
            )
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@market_share_agent.output_validator
def check_sources_read(ctx: RunContext[Deps], data: MarketShareResult) -> MarketShareResult:
    """Layer 1.5 -- every source must EXACTLY match a page actually web_read (no laundering)."""
    if not data.shares:
        return data
    read = read_urls(ctx.messages)
    if not read:
        return data  # nothing read yet; let the other layers handle it
    bad = unread_sources([s.source for s in data.shares], read)
    if bad:
        raise ModelRetry(
            "Every `source` must EXACTLY match (same host+path) a page you opened with "
            "web_read. These do NOT -- you never opened them:\n- "
            + "\n- ".join(sorted(set(bad)))
            + "\nUse one of the EXACT URLs you actually read:\n- "
            + "\n- ".join(read)
        )
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
            "Your shares are close but not done. Fix these -- and if an issue is about a "
            "missing player or stale/insufficient data, run MORE searches and `web_read` "
            "another source to fill it in (don't just resubmit the same list):\n- "
            + "\n- ".join(verdict.issues)
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
