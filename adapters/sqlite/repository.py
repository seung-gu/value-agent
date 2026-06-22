"""SqliteRepository -- a generic Repository[T] over a SqliteTable (dump on save, validate on get).

Replaces the per-domain sqlite repositories: parameterized by the pydantic model (for
validation) and a key function (entity -> key string). `SqliteStorage` builds one per table.
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

from pydantic import BaseModel

from adapters.sqlite.base import SqliteTable
from ports.repository import Repository

T = TypeVar("T", bound=BaseModel)


class SqliteRepository(Repository[T], Generic[T]):
    def __init__(self, table: SqliteTable, model: type[T], key: Callable[[T], str]):
        self._t = table
        self._model = model
        self._key = key

    async def get(self, key: str, period: str) -> T | None:
        data = await self._t.get(key, period)
        return self._model.model_validate(data) if data is not None else None

    async def save(self, entity: T, period: str) -> None:
        await self._t.save(self._key(entity), period, entity.model_dump())
