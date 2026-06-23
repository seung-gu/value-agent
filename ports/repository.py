"""Repository ports -- two shapes matching the table split (static master / time-series).

- `StaticRepository[T]`: code-keyed master rows, no period (gics_reference, sub_industry,
  company). Upsert by code, get by code, list with simple equality filters.
- `TimeSeriesRepository[T]`: rows accumulated per `period` (sub_industry_metric, market_share,
  company_portfolio). A parent (e.g. one sub_industry) owns many rows per period; that
  (parent, period) set is replaced atomically on re-run. `history` returns all periods.

The orchestrator depends on these interfaces; the sqlite adapter implements them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class StaticRepository(ABC, Generic[T]):
    """A code-keyed master store (no period)."""

    @abstractmethod
    async def upsert(self, entity: T) -> None:
        """Insert or update one row by its primary-key code."""
        ...

    @abstractmethod
    async def get(self, code: str) -> T | None:
        """Fetch one row by code, or None."""
        ...

    @abstractmethod
    async def list(self, **where: str) -> list[T]:
        """All rows, optionally filtered by equality (e.g. list(group_code='4530'))."""
        ...


class TimeSeriesRepository(ABC, Generic[T]):
    """A period-keyed store; a parent owns many rows per period (replaced atomically)."""

    @abstractmethod
    async def replace(self, parent: str, period: str, rows: list[T]) -> None:
        """Replace the parent's whole row-set for that period (idempotent re-run)."""
        ...

    @abstractmethod
    async def get(self, parent: str, period: str) -> list[T]:
        """The parent's rows for one period."""
        ...

    @abstractmethod
    async def history(self, parent: str) -> list[T]:
        """The parent's rows across all periods, ordered by period (the trend)."""
        ...
