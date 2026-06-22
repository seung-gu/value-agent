"""LocalBlobStore -- BlobStore backed by files under a directory (<root>/<url-hash>).

Same interface as R2BlobStore, so swapping is a one-line change at the composition root.
Handles text or binary (PDF) alike -- it's just bytes. Hashes the url to a flat filename.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class LocalBlobStore:
    """BlobStore backed by local files. url -> <root>/<sha256(url)>."""

    def __init__(self, root: str | Path = "data/raw"):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return self._root / _key(url)

    async def get(self, url: str) -> bytes | None:
        p = self._path(url)
        # local file IO is fast but sync -- run off the event loop so the fan-out isn't blocked
        return await asyncio.to_thread(lambda: p.read_bytes() if p.exists() else None)

    async def put(self, url: str, data: bytes) -> None:
        await asyncio.to_thread(self._path(url).write_bytes, data)
