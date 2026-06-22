"""Repository port -- generic persist/fetch for a domain entity, keyed by (key, period).

One generic interface for every domain repository (sector / sub-industry / company): the
adapter is parameterized by the entity type + a key function, so there's no per-domain
boilerplate. Freshness is the `period` in the key (ask for 2026-Q2; a 2026-Q1 row misses).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class Repository(ABC, Generic[T]):
    """Stores a domain entity `T` per (key, period)."""

    @abstractmethod
    async def get(self, key: str, period: str) -> T | None:
        """Cached entity for (key, period), or None if absent / different period."""
        ...

    @abstractmethod
    async def save(self, entity: T, period: str) -> None:
        """Store (upsert) the entity; the adapter derives the key from it."""
        ...
