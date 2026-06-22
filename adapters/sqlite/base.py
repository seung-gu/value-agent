"""Shared SQLite plumbing for the per-domain repositories.

`SqliteTable` is a generic (entity, period) -> JSON store over ONE table; each repository
wraps it and adds the domain typing (dump on save, validate on get). `open_connection`
opens the shared db (one file, one connection) with the WAL/concurrency PRAGMAs and creates
the per-domain tables.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

TABLES = ("sector", "sub_industry", "company")


def _normalize(entity: str) -> str:
    """Canonicalize an entity key so trivial drift ('  Cloud  Infrastructure ') still hits.

    A missed alias only costs a re-research (safe but leaky) -- never wrong data.
    """
    return " ".join(entity.lower().split())


class SqliteTable:
    """Generic (entity, period) -> dict store over one table. Repos wrap this and add typing."""

    def __init__(self, conn: aiosqlite.Connection, table: str):
        self._db = conn
        self._table = table  # from the hardcoded TABLES tuple -- never user input

    async def get(self, entity: str, period: str) -> dict | None:
        cur = await self._db.execute(
            f"SELECT value_json FROM {self._table} WHERE entity=? AND period=?",
            (_normalize(entity), period),
        )
        row = await cur.fetchone()
        return json.loads(row["value_json"]) if row else None

    async def save(self, entity: str, period: str, value: dict) -> None:
        await self._db.execute(
            f"""
            INSERT INTO {self._table} (entity, period, value_json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity, period) DO UPDATE SET
                value_json = excluded.value_json,
                fetched_at = excluded.fetched_at
            """,
            (
                _normalize(entity),
                period,
                json.dumps(value, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await self._db.commit()


async def open_connection(db_path: str | Path) -> aiosqlite.Connection:
    """Open the shared sqlite connection (WAL + busy_timeout) and create the per-domain tables."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    # WAL lets readers run during a write; busy_timeout serializes writers without SQLITE_BUSY;
    # NORMAL trims fsync cost safely under WAL. Fan-out writes are tiny + serialized -> invisible.
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA synchronous=NORMAL")
    for table in TABLES:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                entity     TEXT NOT NULL,    -- normalized entity key
                period     TEXT NOT NULL,    -- freshness bucket, e.g. '2026-Q2'
                value_json TEXT NOT NULL,    -- the domain entity, dumped
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (entity, period)
            )
            """
        )
    await conn.commit()
    return conn
