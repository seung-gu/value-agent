"""
섹터 분석 agent (탑다운 출발점).

미국 증시 GICS 섹터 하나를 받아, 웹 검색(Serper)으로 성장성·잠재성을 조사하고
SectorAnalysis 구조로 결과를 반환한다. 경쟁력 있는 기업 발굴까지 포함.

검증 2층 (둘 다 @output_validator에서 수행 → 실패 시 ModelRetry로 PydanticAI가 자동 재조사):
- 1층 (형식·근거): 결정적 체크 (출처 개수, 경쟁사, 필수 필드).
- 2층 (주관 품질): 범용 judge_agent에 섹터 rubric을 넘겨 출처 신뢰도·최신성 판정.

실행:
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

load_dotenv()  # .env에서 LLM_MODEL, ANTHROPIC_API_KEY, SERPER_API_KEY 로드

# 관측(observability): agent·LLM·tool 호출을 logfire로 추적.
# send_to_logfire="if-token-present" → 토큰 없으면 조용히 로컬, 있으면 클라우드 전송.
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()  # 모든 agent 실행 계측
logfire.instrument_httpx()         # Serper 호출까지 추적


# ---------------------------------------------------------------------------
# 의존성 (deps) — 런타임에 주입
# ---------------------------------------------------------------------------
@dataclass
class Deps:
    search: SearchClient  # 키·http를 품은 검색 클라이언트 (Serper 등)


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------
sector_agent = Agent(
    os.environ.get("LLM_MODEL", "anthropic:claude-sonnet-4-6"),
    deps_type=Deps,
    output_type=SectorAnalysis,
    retries=2,  # output_validator(ModelRetry) 재조사 예산 — 형식·품질 검증 실패 시 재시도
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
    """오늘 날짜·현재 분기·조사 권장 기간(현재 분기 ±4Q)을 반환.

    agent가 '최신' 데이터를 정확한 기준으로 찾도록 실제 현재 날짜를 알려준다.
    deps가 필요 없으므로 tool_plain(컨텍스트 없는 도구)으로 등록한다.
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
    """웹 검색(정제된 결과). 섹터 시장규모·CAGR·경쟁사 조사에 사용.

    실제 검색·정제는 deps의 SearchClient가 담당한다(Serper 등 백엔드 교체 가능).
    """
    return await ctx.deps.search.search(query)


# ---------------------------------------------------------------------------
# 출력 검증 — @output_validator 2개. 정의 순서대로 실행되며 둘 다 통과해야 한다.
# 실패 시 ModelRetry를 던지면 PydanticAI가 피드백을 모델에 주고 자동 재조사(retries=2).
# format이 먼저 돌아서, 형식이 틀리면 비싼 judge(LLM) 호출을 건너뛴다.
# 참고: https://ai.pydantic.dev/output/  (output validators & ModelRetry)
# ---------------------------------------------------------------------------
@sector_agent.output_validator
def check_format(data: SectorAnalysis) -> SectorAnalysis:
    """1층 — 결정적 형식·근거 체크 (계산만 → 동기)."""
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


# 섹터 분석 전용 rubric — 도메인 기준은 호출자가 소유한다(judge_agent는 범용).
# 출력에서 검증 가능한 '측정 가능한 criteria'로 작성(업계 표준).
SECTOR_RUBRIC = (
    "1) Sources are from reputable outlets (Gartner, Reuters, SEC, Bloomberg, "
    "established research firms, or company IR pages) and the market_size/cagr "
    "figures are attributable to them — not vague or invented.\n"
    "2) Figures reflect recent data (roughly current quarter +/- 4 quarters), "
    "not stale numbers several years old."
)


@sector_agent.output_validator
async def check_quality(ctx: RunContext[Deps], data: SectorAnalysis) -> SectorAnalysis:
    """2층 — 범용 judge에 섹터 rubric을 넘겨 주관 품질 판정 (judge LLM 호출 → 비동기)."""
    verdict = await judge(SECTOR_RUBRIC, data, usage=ctx.usage)  # usage 전달 → 토큰 합산
    if not verdict.passed:
        raise ModelRetry("Improve quality:\n- " + "\n- ".join(verdict.issues))
    return data


# ---------------------------------------------------------------------------
# 실행
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
