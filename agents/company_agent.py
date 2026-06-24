"""Company agent -- the WEB FALLBACK for one company's financials + revenue portfolio.

For US-listed firms the orchestrator pulls both from edgartools directly (deterministic, no LLM).
This agent only runs when there's no SEC CIK (foreign / unlisted): it researches the same two
tables from public reports. It MIRRORS the EDGAR shape so the orchestrator stores the rows
identically -- company-level financial `accounts` (revenue / operating_income / net_income /
operating_cash_flow) + revenue `streams` (product/segment), all as ABSOLUTE amounts in the
currency the company reports, for one fiscal period.

Validation (each @output_validator -> ModelRetry, retries=4):
- Layer 1 (format): a source + as_of are present; the portfolio streams sum to ~revenue.
- Layer 1.5 (sources): every source EXACTLY matches a page actually web_read (no laundering).
- Layer 2 (quality): the company rubric via the generic judge.

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

# The four company-level accounts we want (same names EDGAR uses, so the tables line up).
KNOWN_ACCOUNTS = ("revenue", "operating_income", "net_income", "operating_cash_flow")


class FinancialFinding(BaseModel):
    """One company-level financial account for the period (absolute, reported currency)."""

    account: str          # one of KNOWN_ACCOUNTS
    amount: float         # may be negative (e.g. a net loss)
    source: str = ""      # URL of a page actually opened with web_read


class StreamFinding(BaseModel):
    """One revenue stream -- product/segment -- for the period (absolute, reported currency)."""

    stream: str                  # e.g. "iPhone", "Cloud", "Advertising"
    amount: float = Field(ge=0)  # revenue attributed to the stream
    source: str = ""


class CompanyResult(BaseModel):
    """A company's financials + revenue portfolio for one fiscal period (streams sum to revenue)."""

    financials: list[FinancialFinding] = Field(default_factory=list)
    portfolio: list[StreamFinding] = Field(default_factory=list)
    as_of: str = ""  # the fiscal period the figures are FROM, read off the report (e.g. "FY2025")


company_agent = Agent(
    COMPANY_MODEL,
    deps_type=Deps,
    output_type=CompanyResult,
    retries=4,
    system_prompt=(
        "You research ONE company's FINANCIALS and its REVENUE PORTFOLIO (segment breakdown) "
        "from public reports, for its most recent fiscal year.\n"
        "FIRST call `get_today` to anchor on today's date, then search for the latest annual "
        "report / 20-F / IR results -- not your training-cutoff year.\n"
        "WHAT TO RETURN -- ABSOLUTE money in the currency the company reports, NOT percentages:\n"
        "- `financials`: the company-level accounts for the period. Use EXACTLY these account "
        "names: 'revenue', 'operating_income', 'net_income', 'operating_cash_flow'. Omit any "
        "you cannot source (don't guess).\n"
        "- `portfolio`: revenue split into the company's reported product/business STREAMS "
        "(e.g. iPhone, Cloud, Advertising), each as the stream's revenue AMOUNT. The streams "
        "should add up to roughly total revenue; put any remainder in a single 'Others' stream.\n"
        "SEARCH DISCIPLINE:\n"
        "1) Keep each `web_search` SHORT: <=6 plain words, NO quote marks, no guessed page "
        "titles (e.g. 'Samsung 2025 annual report revenue segment').\n"
        "2) Financials AND the segment table usually live in the SAME annual report / 20-F / "
        "IR deck -- find that one page, `web_read` it, and pull BOTH from it (one read, not "
        "two searches).\n"
        "3) On a miss, switch ANGLE or SOURCE -- never reword the same query. If a few angles "
        "come up dry, STOP and return what you have (empty lists are fine) -- do NOT estimate "
        "or pad.\n"
        "RECENCY: set `as_of` to the fiscal period the figures are FROM (read it off the "
        "report), as a BARE period only -- exactly 'FY2025' or '2025-Qn', no extra words.\n"
        "HONESTY: use ONLY source-backed figures. Each `source` MUST be the URL of a page you "
        "ACTUALLY opened with web_read -- never cite a link you only saw in search results "
        "(it may be dead/paywalled), and never invent amounts. Return EMPTY lists rather than "
        "guessing."
    ),
)


@company_agent.system_prompt
def _today_note() -> str:
    """Inject today's date every run so the model anchors on the real year, not 2024."""
    now = datetime.now(timezone.utc)
    return (
        f"Today is {now:%Y-%m-%d}; the current year is {now.year}. Put {now.year} or "
        f"{now.year - 1} (or the latest fiscal year) in your search queries -- NEVER an "
        "older year like 2024 unless explicitly asked."
    )


# Shared agent tools (tools/): get_today anchors on the date; web_search/web_read delegate
# to the SearchClient adapter in Deps.
company_agent.tool_plain(get_today)
company_agent.tool(web_search)
company_agent.tool(web_read)


# Company rubric -- domain criteria for the generic judge (sourcing + the portfolio↔revenue tie).
COMPANY_RUBRIC = (
    "1) SOURCING: each financial figure and each portfolio stream is attributable to a "
    "reputable source (the company's annual report / 20-F / 10-K / IR site, Reuters, "
    "Bloomberg). Judge by source-domain reputation only.\n"
    "2) PORTFOLIO SUM: the portfolio streams' amounts add up to roughly total revenue (an "
    "'Others' stream is fine). No invented numbers -- if a breakdown isn't sourced, leave "
    "`portfolio` empty instead.\n"
    "3) SOURCE PRESENT: a `source` is present on every financial figure and stream.\n"
    "4) COVERAGE: `revenue` is present, and the company's main reported segments are present. "
    "If a clearly material segment is missing, FAIL and name it.\n"
    "5) RECENCY: `as_of` names the fiscal period and is recent -- today is {today}. Figures "
    "more than ~1 year old (e.g. 2023 numbers when today is 2026) are STALE: FAIL them and "
    "require current-period data."
)


@company_agent.output_validator
def check_format(data: CompanyResult) -> CompanyResult:
    """Layer 1 -- deterministic checks (source + as_of present, portfolio ties to revenue)."""
    problems: list[str] = []
    if data.financials or data.portfolio:
        sources = [f.source for f in data.financials] + [s.source for s in data.portfolio]
        if not any(s.strip() for s in sources):
            problems.append("Provide at least one source for a non-empty result.")
        if not data.as_of.strip():
            problems.append(
                "Set `as_of` to the fiscal period the figures are from (read from the "
                "report, e.g. 'FY2025')."
            )
    # When both revenue and a portfolio are present, the streams must tie to revenue (~±20%).
    revenue = next((f.amount for f in data.financials if f.account == "revenue"), None)
    if revenue and revenue > 0 and data.portfolio:
        total = sum(s.amount for s in data.portfolio)
        if not 0.8 <= total / revenue <= 1.2:
            problems.append(
                f"Portfolio streams sum to {total:,.0f} but revenue is {revenue:,.0f}; they "
                "must roughly match (add an 'Others' stream for the remainder)."
            )
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@company_agent.output_validator
def check_sources_read(ctx: RunContext[Deps], data: CompanyResult) -> CompanyResult:
    """Layer 1.5 -- every source must EXACTLY match a page actually web_read (no laundering)."""
    items = list(data.financials) + list(data.portfolio)
    if not items:
        return data
    read = read_urls(ctx.messages)
    if not read:
        return data  # nothing read yet; let the other layers handle it
    bad = unread_sources([i.source for i in items], read)
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
async def check_quality(ctx: RunContext[Deps], data: CompanyResult) -> CompanyResult:
    """Layer 2 -- pass the company rubric to the generic judge (skip when fully empty)."""
    if not (data.financials or data.portfolio):
        return data  # an empty result is acceptable (no source-backed data found)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    verdict = await judge(COMPANY_RUBRIC.format(today=today), data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry(
            "Your result is close but not done. Fix these -- and if an issue is about a "
            "missing segment or stale/insufficient data, run MORE searches and `web_read` "
            "another source to fill it in (don't just resubmit the same data):\n- "
            + "\n- ".join(verdict.issues)
        )
    return data


async def research_company(
    name: str, *, search: SearchClient, usage: RunUsage | None = None
) -> CompanyResult:
    """Research one company's financials + revenue portfolio from the web -> CompanyResult."""
    result = await company_agent.run(
        f"Research the financials and revenue portfolio of {name}.",
        deps=Deps(search=search),
        usage=usage,
    )
    return result.output


async def main() -> None:
    from adapters.serper.search_client import SerperClient

    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        result = await research_company("Samsung Electronics", search=search)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
