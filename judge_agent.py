"""범용 judge agent — 어떤 agent의 출력이든 주어진 rubric으로 평가한다.

특정 도메인(섹터 분석)에 묶이지 않는다. 호출자가 평가할 output과 rubric을 주면,
judge_agent가 rubric 충족 여부를 Verdict(passed, issues)로 판정한다 → 재사용 가능.
(업계 표준 'tiered hybrid' 검증에서 deterministic 체크 다음의 'LLM-as-judge' 층.)

쓰는 쪽 예:
    from judge_agent import judge
    verdict = await judge(SECTOR_RUBRIC, result, usage=ctx.usage)
    if not verdict.passed:
        raise ModelRetry("Improve:\\n- " + "\\n- ".join(verdict.issues))

참고:
- Agent / output_type:            https://ai.pydantic.dev/agent/
- output validators / ModelRetry: https://ai.pydantic.dev/output/
- LLM-as-a-judge 패턴:            https://deepeval.com/guides/guides-llm-as-a-judge
"""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.usage import RunUsage

load_dotenv()  # judge_agent 생성 전에 .env 로드 (import 순서 무관)

# bias를 줄이려면 검증 대상 agent와 '다른' 모델 family를 쓰는 게 권장된다(self-preference 회피).
JUDGE_MODEL = "anthropic:claude-sonnet-4-6"


class Verdict(BaseModel):
    passed: bool
    issues: list[str]  # 불합격 시 고칠 점 (재조사 피드백으로 사용)


# rubric에 묶이지 않는 범용 verifier. rubric은 매 호출 시 user 메시지로 전달된다.
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
    model_settings={"temperature": 0.0},  # 일관된 판정
)


@judge_agent.output_validator
def _sane(v: Verdict) -> Verdict:
    """불합격인데 사유가 없으면 재조사 피드백이 비므로, judge에게 다시 내게 한다."""
    if not v.passed and not v.issues:
        raise ModelRetry("If passed is false, list at least one concrete issue.")
    return v


async def judge(rubric: str, output: Any, *, usage: RunUsage | None = None) -> Verdict:
    """임의의 output을 주어진 rubric으로 평가 → Verdict.

    - output: BaseModel이면 JSON으로 직렬화해 보여주고, 아니면 str()로 변환.
    - usage: 넘기면 호출자 run의 토큰 사용량에 합산된다(agent delegation).

    날짜·최신성 같은 '도메인 기준'은 rubric이 갖는다 (judge_agent는 범용이므로).
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
