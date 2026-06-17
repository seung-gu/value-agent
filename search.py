"""웹 검색 클라이언트 — 검색 백엔드 어댑터.

`SearchClient`(Protocol)로 인터페이스(포트)를 정의하고, `SerperClient`가 구현한다.
나중에 Brave/Tavily/MCP 등으로 바꾸려면 같은 Protocol을 따르는 구현만 추가하면 된다.
(agent 코드는 SearchClient 타입에만 의존하므로 바뀌지 않는다.)
"""

from __future__ import annotations

from typing import Protocol

import httpx


class SearchClient(Protocol):
    """검색 백엔드 인터페이스. query를 받아 '정제된' 텍스트 결과를 반환한다."""

    async def search(self, query: str) -> str: ...


class SerperClient:
    """Serper(google.serper.dev) 기반 SearchClient 구현.

    API 키와 http client를 생성 시점에 한 번 주입받아 캡슐화한다(호출부는 query만).
    """

    def __init__(self, api_key: str, http: httpx.AsyncClient, *, num: int = 8):
        self._key = api_key
        self._http = http
        self._num = num  # 검색당 organic 결과 개수

    async def search(self, query: str) -> str:
        resp = await self._http.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self._key},
            json={"q": query, "num": self._num},
            timeout=30.0,
        )
        resp.raise_for_status()
        return self._clean(resp.json())

    @staticmethod
    def _clean(data: dict) -> str:
        """organic 결과의 제목·날짜·스니펫·링크만 추려 토큰을 절약한다.

        raw JSON의 knowledgeGraph·relatedSearches·sitelinks 등 노이즈는 버린다.
        answerBox(직접 답)가 있으면 맨 앞에 붙인다.
        """
        lines: list[str] = []
        answer = data.get("answerBox") or {}
        if snippet := (answer.get("answer") or answer.get("snippet")):
            lines.append(f"[answer] {snippet}")
        for h in data.get("organic", []):
            title = h.get("title", "")
            snippet = h.get("snippet", "")
            link = h.get("link", "")
            date = f" ({h['date']})" if h.get("date") else ""
            lines.append(f"- {title}{date}: {snippet} <{link}>")
        return "\n".join(lines) if lines else "(no results)"
