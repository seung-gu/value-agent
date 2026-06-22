"""Blob store port -- url -> bytes (the scraped page/PDF, stored as-is).

Layer B: lets `SearchClient.scrape()` serve a URL from storage instead of re-fetching, and
keeps the raw source for re-parsing / future RAG. `LocalBlobStore` / `R2BlobStore` implement it.
The adapter hashes the url to a storage key internally -- callers just pass the url.
"""

from __future__ import annotations

from typing import Protocol


class BlobStore(Protocol):
    """url -> raw bytes. Implemented by LocalBlobStore (files) and R2BlobStore (Cloudflare R2)."""

    async def get(self, url: str) -> bytes | None: ...
    async def put(self, url: str, data: bytes) -> None: ...
