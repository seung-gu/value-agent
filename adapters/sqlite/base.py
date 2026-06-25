"""SQLite plumbing -- shared connection + the 7-table schema (DDL).

The 7 domain tables split into two shapes:
- STATIC (gics_reference, sub_industry, company): code-keyed master rows, no period.
- TIME-SERIES (sub_industry_kpi, market_share, company_portfolio, company_financials): `period`
  is part of the PK, so rows accumulate one set per period (schema never changes -- only INSERTs).

DDL is explicit here (one CREATE per table -- the schemas genuinely differ); the generic
CRUD lives in repository.py. Column names == pydantic field names, so the repos can map
rows <-> models with model_dump/model_validate and no per-table SQL.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

# Explicit per-table DDL. FKs document the 1:N / N:M wiring and guard integrity.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS gics_reference (
    group_code  TEXT PRIMARY KEY,        -- '4530'
    sector_code TEXT NOT NULL,           -- '45'
    sector_name TEXT NOT NULL,
    group_name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sub_industry (
    sub_code   TEXT PRIMARY KEY,         -- surrogate '4530-01' (split child: '4530-06-S01')
    group_code TEXT NOT NULL REFERENCES gics_reference(group_code),
    name       TEXT NOT NULL,
    definition TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS company (
    company_code TEXT PRIMARY KEY,       -- surrogate 'C0001'
    name         TEXT NOT NULL,
    ticker       TEXT UNIQUE             -- nullable; UNIQUE dedups listed companies + EDGAR map
);

CREATE TABLE IF NOT EXISTS sub_industry_kpi (
    sub_code    TEXT NOT NULL REFERENCES sub_industry(sub_code),
    period      TEXT NOT NULL,           -- '2026-Q2'
    cagr        REAL,                    -- nullable (may be unknown)
    penetration REAL,
    source      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sub_code, period)
);

CREATE TABLE IF NOT EXISTS market_share (
    sub_code     TEXT NOT NULL REFERENCES sub_industry(sub_code),
    company_code TEXT NOT NULL REFERENCES company(company_code),
    period       TEXT NOT NULL,
    percentage   REAL NOT NULL,
    source       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sub_code, company_code, period)
);

CREATE TABLE IF NOT EXISTS company_portfolio (
    company_code TEXT NOT NULL REFERENCES company(company_code),
    period       TEXT NOT NULL,
    stream       TEXT NOT NULL,          -- 'iPhone', 'Services', ...
    amount       REAL NOT NULL,          -- USD revenue attributed to the stream
    source       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (company_code, stream, period)
);

CREATE TABLE IF NOT EXISTS company_financials (
    company_code TEXT NOT NULL REFERENCES company(company_code),
    period       TEXT NOT NULL,
    account      TEXT NOT NULL,          -- 'revenue', 'net_income', ...
    amount       REAL NOT NULL,          -- USD (may be negative, e.g. net loss)
    source       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (company_code, account, period)
);
"""


async def open_connection(db_path: str | Path) -> aiosqlite.Connection:
    """Open the shared sqlite connection (WAL + FK + concurrency PRAGMAs) and create the tables."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    # WAL lets readers run during a write; busy_timeout serializes writers without SQLITE_BUSY;
    # NORMAL trims fsync cost safely under WAL. foreign_keys enforces the REFERENCES above.
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(_SCHEMA)
    await conn.commit()
    return conn
