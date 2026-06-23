"""Generic SQLite repositories -- one for static tables, one for time-series tables.

Both map rows <-> pydantic models generically (column names == field names), so a single
class serves all three static tables and another serves all three time-series tables --
no per-table SQL. `table`/`pk`/`parent` come from the hardcoded wiring in __init__.py
(never user input), so the f-string SQL is safe.
"""

from __future__ import annotations

from typing import Generic, TypeVar

import aiosqlite
from pydantic import BaseModel

from ports.repository import StaticRepository, TimeSeriesRepository

T = TypeVar("T", bound=BaseModel)


def _insert_sql(table: str, cols: list[str]) -> str:
    placeholders = ", ".join("?" for _ in cols)
    return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"


class StaticTable(StaticRepository[T], Generic[T]):
    """Code-keyed master table: upsert by PK, get by code, list with equality filters."""

    def __init__(self, conn: aiosqlite.Connection, table: str, model: type[T], pk: str):
        self._db = conn
        self._table = table
        self._model = model
        self._pk = pk

    async def upsert(self, entity: T) -> None:
        data = entity.model_dump()
        cols = list(data.keys())
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != self._pk)
        await self._db.execute(
            f"{_insert_sql(self._table, cols)} "
            f"ON CONFLICT({self._pk}) DO UPDATE SET {updates}",
            tuple(data.values()),
        )
        await self._db.commit()

    async def upsert_many(self, entities: list[T]) -> None:
        for e in entities:  # small + infrequent (seed / discovery) -> a simple loop is fine
            await self.upsert(e)

    async def get(self, code: str) -> T | None:
        cur = await self._db.execute(
            f"SELECT * FROM {self._table} WHERE {self._pk}=?", (code,)
        )
        row = await cur.fetchone()
        return self._model.model_validate(dict(row)) if row else None

    async def list(self, **where: str) -> list[T]:
        sql = f"SELECT * FROM {self._table}"
        params: tuple = ()
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
            params = tuple(where.values())
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()
        return [self._model.model_validate(dict(r)) for r in rows]


class TimeSeriesTable(TimeSeriesRepository[T], Generic[T]):
    """Period-keyed table: a parent's (parent, period) row-set is replaced atomically; rows
    accumulate across periods. `parent` is the owner column (e.g. 'sub_code')."""

    def __init__(self, conn: aiosqlite.Connection, table: str, model: type[T], parent: str):
        self._db = conn
        self._table = table
        self._model = model
        self._parent = parent

    async def replace(self, parent: str, period: str, rows: list[T]) -> None:
        await self._db.execute(
            f"DELETE FROM {self._table} WHERE {self._parent}=? AND period=?", (parent, period)
        )
        for r in rows:
            data = r.model_dump()
            await self._db.execute(_insert_sql(self._table, list(data.keys())), tuple(data.values()))
        await self._db.commit()

    async def get(self, parent: str, period: str) -> list[T]:
        cur = await self._db.execute(
            f"SELECT * FROM {self._table} WHERE {self._parent}=? AND period=?", (parent, period)
        )
        rows = await cur.fetchall()
        return [self._model.model_validate(dict(r)) for r in rows]

    async def history(self, parent: str) -> list[T]:
        cur = await self._db.execute(
            f"SELECT * FROM {self._table} WHERE {self._parent}=? ORDER BY period", (parent,)
        )
        rows = await cur.fetchall()
        return [self._model.model_validate(dict(r)) for r in rows]
