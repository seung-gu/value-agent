"""SerperClient -- SearchClient adapter backed by Serper (google.serper.dev).

Implements the port's `search()` and the raw `_fetch_page()`; the scrape-caching template
lives in the SearchClient base. Pass a `raw` store to enable Layer-B scrape caching.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import httpx

from ports.search_client import SearchClient

if TYPE_CHECKING:
    from ports.blob_store import BlobStore


class SerperClient(SearchClient):
    """SearchClient backed by Serper. Key + http injected once; callers pass only query/url."""

    def __init__(
        self,
        api_key: str,
        http: httpx.AsyncClient,
        *,
        num: int = 8,
        blobs: BlobStore | None = None,
    ):
        super().__init__(blobs=blobs)
        self._key = api_key
        self._http = http
        self._num = num  # organic results per search

    async def search(self, query: str) -> str:
        q = (query or "").strip()
        print(f"  [web_search] {q}", file=sys.stderr, flush=True)  # diagnostic: log each query
        if not q:
            return "(empty query -- nothing searched)"
        try:
            resp = await self._http.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self._key},
                json={"q": q, "num": self._num},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            # One failed search must not kill the pipeline -- report it back to the agent.
            return f"(search failed: {e})"
        return self._clean(resp.json())

    async def _fetch_page(self, url: str) -> str:
        """Fetch a page's ACTUAL content via Serper's scrape endpoint (only on a cache miss).

        The [scrape] log thus marks a REAL network scrape (a cache hit prints nothing).
        """
        u = (url or "").strip()
        if not u:
            return "(empty url -- nothing to read)"
        print(f"  [scrape] {u}", file=sys.stderr, flush=True)
        try:
            resp = await self._http.post(
                "https://scrape.serper.dev",
                headers={"X-API-KEY": self._key},
                json={"url": u},
                timeout=45.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"(scrape failed: {e})"
        data = resp.json()
        return data.get("markdown") or data.get("text") or "(no content)"

    @staticmethod
    def _clean(data: dict) -> str:
        """Keep only title/date/snippet/link of organic results to save tokens."""
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
