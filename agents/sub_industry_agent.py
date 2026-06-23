"""Sub-industry agent -- identifies the analysis units (sub-industries) under an industry group.

Given a GICS industry group (e.g. 'Semiconductors & Semiconductor Equipment'), it researches
the web (ReAct: search -> read) and proposes the sub-industries to analyze -- splitting along
the value chain where the GICS standard is too coarse (e.g. semis -> Foundry / Memory /
Fabless / Equipment). GICS sub-industries are a REFERENCE only. Output is name + definition;
the surrogate sub_code is assigned on save. Re-runnable with human feedback (the HITL refine
loop behind the taxonomy API).

Run:
    uv run python -m agents.sub_industry_agent
"""

from __future__ import annotations

import asyncio
import os

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

load_dotenv()  # load SUB_INDUSTRY_MODEL / LLM_MODEL / keys from .env

SUB_INDUSTRY_MODEL = os.environ.get(
    "SUB_INDUSTRY_MODEL", os.environ.get("LLM_MODEL", "openai:gpt-5-mini")
)


class SubIndustryFinding(BaseModel):
    """One proposed sub-industry under an industry group (code assigned on save, not here)."""

    name: str               # e.g. "Foundry"
    definition: str = ""    # one-line scope
    rationale: str = ""     # why it's a distinct analysis unit (for human review)


class SubIndustryProposal(BaseModel):
    """The proposed sub-industry list for one industry group (3-8 distinct units)."""

    subs: list[SubIndustryFinding] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


sub_industry_agent = Agent(
    SUB_INDUSTRY_MODEL,
    deps_type=Deps,
    output_type=SubIndustryProposal,
    retries=2,
    system_prompt=(
        "You identify the SUB-INDUSTRIES (analysis units) inside ONE GICS industry group. "
        "FIRST call `get_today`. Given an industry group, use `web_search` then `web_read` to "
        "research how that group is actually segmented by the market, then propose 3-8 "
        "sub-industries that are MEANINGFUL, MUTUALLY-DISTINCT units -- split along the value "
        "chain where the GICS standard is too coarse (e.g. Semiconductors -> Foundry / Memory "
        "/ Fabless chip design / Semiconductor equipment). GICS sub-industries are a REFERENCE "
        "ONLY; refine them. For each, give a short `definition` and a `rationale` (why it's a "
        "distinct unit). Prefer STABLE segments a market analyst would recognize, not transient "
        "hype. Record `sources`. If the user gives feedback on a previous list, revise it."
    ),
)


# Shared agent tools (tools/): get_today anchors on the date; web_search/web_read delegate
# to the SearchClient adapter in Deps.
sub_industry_agent.tool_plain(get_today)
sub_industry_agent.tool(web_search)
sub_industry_agent.tool(web_read)


PROPOSAL_RUBRIC = (
    "1) `subs` lists 3-8 real, mutually-distinct sub-industries that a market analyst would "
    "recognize as segments of THIS industry group (judge by domain knowledge + cited sources).\n"
    "2) Each has a clear one-line definition; together they cover the group without large "
    "overlap.\n"
    "3) `sources` are present, and the segments are stable analysis units, not transient hype."
)


@sub_industry_agent.output_validator
def check_format(data: SubIndustryProposal) -> SubIndustryProposal:
    """Layer 1 -- deterministic checks (count, distinct names, sources)."""
    problems: list[str] = []
    if not 3 <= len(data.subs) <= 8:
        problems.append("Propose between 3 and 8 sub-industries.")
    names = [s.name.strip().lower() for s in data.subs]
    if len(set(names)) != len(names):
        problems.append("Sub-industry names must be distinct.")
    if data.subs and not data.sources:
        problems.append("Provide at least one source URL.")
    if problems:
        raise ModelRetry(" ".join(problems))
    return data


@sub_industry_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: SubIndustryProposal) -> SubIndustryProposal:
    """Layer 2 -- pass the proposal rubric to the generic judge (skip when empty)."""
    if not data.subs:
        return data
    verdict = await judge(PROPOSAL_RUBRIC, data, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry(
            "Your previous list was largely correct. Revise ONLY these issues:\n- "
            + "\n- ".join(verdict.issues)
        )
    return data


async def propose_sub_industries(
    group_name: str,
    *,
    search: SearchClient,
    feedback: str | None = None,
    current: SubIndustryProposal | None = None,
    usage: RunUsage | None = None,
) -> SubIndustryProposal:
    """Propose (or refine) the sub-industries under an industry group.

    feedback/current: in the HITL loop, pass the prior list + the user's comment to revise.
    """
    prompt = f"Identify the sub-industries inside the '{group_name}' industry group."
    if current is not None and feedback:
        listing = "\n".join(f"- {s.name}: {s.definition}" for s in current.subs)
        prompt = (
            f"Current proposed sub-industries for '{group_name}':\n{listing}\n\n"
            f"Revise the list per this feedback:\n{feedback}"
        )
    result = await sub_industry_agent.run(prompt, deps=Deps(search=search), usage=usage)
    return result.output


async def main() -> None:
    from adapters.serper.search_client import SerperClient

    async with httpx.AsyncClient() as client:
        search = SerperClient(os.environ["SERPER_API_KEY"], client)
        result = await propose_sub_industries(
            "Semiconductors & Semiconductor Equipment", search=search
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
