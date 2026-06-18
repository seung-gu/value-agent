"""Generic judge agent -- evaluates any agent's output against a given rubric.

Not tied to a specific domain (sector analysis). The caller passes the output to
evaluate and a rubric; judge_agent returns a Verdict(passed, issues) on whether the
output satisfies the rubric -> reusable. (This is the 'LLM-as-judge' layer that comes
after deterministic checks in the industry-standard 'tiered hybrid' verification.)

Example:
    from judge_agent import judge
    verdict = await judge(SECTOR_RUBRIC, result, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry("Improve:\\n- " + "\\n- ".join(verdict.issues))

Refs:
- Agent / output_type:            https://ai.pydantic.dev/agent/
- output validators / ModelRetry: https://ai.pydantic.dev/output/
- LLM-as-a-judge pattern:         https://deepeval.com/guides/guides-llm-as-a-judge
"""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.usage import RunUsage

load_dotenv()  # load .env before judge_agent is created (independent of import order)

# To reduce bias, using a different model family from the agent under test is
# recommended (avoids self-preference bias).
JUDGE_MODEL = "anthropic:claude-sonnet-4-6"


class Verdict(BaseModel):
    passed: bool
    issues: list[str]  # fixes to apply when failed (used as re-analysis feedback)


# Generic verifier, not tied to any rubric. The rubric is passed per call as the user message.
judge_agent = Agent(
    JUDGE_MODEL,
    output_type=Verdict,
    system_prompt=(
        "You are a pragmatic, impartial verifier. You are given a RUBRIC and an OUTPUT, "
        "and you judge whether the output satisfies the rubric.\n"
        "IMPORTANT — you are an LLM, so be honest about your limits:\n"
        "- You CANNOT fetch URLs or confirm a link really exists. Judge sources by "
        "DOMAIN reputation only (e.g. gartner.com, reuters.com), never by trying to "
        "verify the exact URL.\n"
        "- You CANNOT fact-check figures against the live world and your training data "
        "may be outdated. Do NOT flag numbers as 'invented'/'fabricated' just because "
        "they differ from your memory — trust figures that cite a reputable source.\n"
        "- Judge ONLY what is checkable from the output itself: source-domain reputation, "
        "internal consistency, completeness, and format.\n"
        "Set passed=true if the output reasonably satisfies the rubric; set passed=false "
        "only for concrete, checkable problems, and list actionable fixes in `issues`."
    ),
    model_settings={"temperature": 0.0},  # consistent verdicts
)


@judge_agent.output_validator
def _sane(v: Verdict) -> Verdict:
    """If failed but no reason is given, the re-analysis feedback would be empty, so make the judge retry."""
    if not v.passed and not v.issues:
        raise ModelRetry("If passed is false, list at least one concrete issue.")
    return v


async def judge(rubric: str, output: Any, *, usage: RunUsage | None = None) -> Verdict:
    """Evaluate an arbitrary output against the given rubric -> Verdict.

    - output: serialized to JSON if a BaseModel, otherwise str().
    - usage: if passed, token usage is added to the caller's run (agent delegation).

    Domain criteria such as date/recency belong to the rubric (judge_agent is generic).
    """
    content = (
        output.model_dump_json(indent=2)
        if isinstance(output, BaseModel)
        else str(output)
    )
    result = await judge_agent.run(
        f"RUBRIC:\n{rubric}\n\nOUTPUT TO EVALUATE:\n{content}",
        usage=usage,
    )
    return result.output
