"""Web search client -- search backend adapter.

`SearchClient` (Protocol) defines the interface (port); `SerperClient` implements it.
To switch to Brave/Tavily/MCP later, just add another implementation of the same
Protocol (agent code only depends on the SearchClient type, so it doesn't change).
"""

from __future__ import annotations

from typing import Protocol

import httpx


class SearchClient(Protocol):
    """Search backend interface. Takes a query, returns 'cleaned' text results."""

    async def search(self, query: str) -> str: ...


class SerperClient:
    """SearchClient implementation backed by Serper (google.serper.dev).

    The API key and http client are injected once at construction and encapsulated
    (callers pass only the query).
    """

    def __init__(self, api_key: str, http: httpx.AsyncClient, *, num: int = 8):
        self._key = api_key
        self._http = http
        self._num = num  # organic results per search

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
        """Keep only title/date/snippet/link of organic results to save tokens.

        Drops raw-JSON noise like knowledgeGraph / relatedSearches / sitelinks.
        Prepends the answerBox (direct answer) if present.
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
