"""Search backend port -- the `SearchClient` interface (with shared scrape caching).

`SearchClient` is the port: `search()` finds URLs, `scrape()` reads a page (served from the
blob store when present). The cache check/store TEMPLATE lives here so every adapter
(SerperClient, a future Brave/Tavily client) shares Layer-B dedup; adapters implement only
`search()` and the raw `_fetch_page()`. Caching is bound to the port, not to any one backend.

`BlobStore` is imported under TYPE_CHECKING so this port keeps no runtime dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ports.blob_store import BlobStore


class SearchClient(ABC):
    """Search backend port. Subclasses implement `search()` and the raw `_fetch_page()`.

    Pass a `blobs` store to dedupe scrapes (Layer B); omit it for no caching.
    """

    def __init__(self, *, blobs: "BlobStore | None" = None):
        self._blobs = blobs

    @abstractmethod
    async def search(self, query: str) -> str:
        """Find URLs + snippets for a query (not cached -- queries rarely repeat)."""
        ...

    async def scrape(self, url: str) -> str:
        """Read a page's FULL content -- served from the blob store (Layer B) when present.

        On a hit there is NO network call; on a miss we fetch via the subclass and store the
        result. This template lives in the port so all backends share it.
        """
        if self._blobs is not None:
            cached = await self._blobs.get(url)
            if cached is not None:
                return cached.decode("utf-8", errors="replace")  # already scraped -> no network
        text = await self._fetch_page(url)
        if self._blobs is not None:
            await self._blobs.put(url, text.encode("utf-8"))
        return text

    @abstractmethod
    async def _fetch_page(self, url: str) -> str:
        """Actually fetch the page content (backend-specific; called only on a cache miss)."""
        ...
