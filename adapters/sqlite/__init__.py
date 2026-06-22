"""SQLite adapters -- the 3 domain repositories (one generic SqliteRepository each) over a shared db.

`SqliteStorage.open(db_path)` opens the connection (+ tables) and exposes the repositories:
`storage.sectors`, `storage.sub_industries`, `storage.companies`. Close once via `close()`.
"""

from __future__ import annotations

from adapters.sqlite.base import SqliteTable, open_connection
from adapters.sqlite.repository import SqliteRepository
from domain.company import CompanyPortfolio
from domain.sector import SectorAnalysis
from domain.sub_industry import SubIndustry
from ports.repository import Repository

__all__ = ["SqliteStorage", "SqliteRepository"]


class SqliteStorage:
    """Owns one sqlite connection and exposes the 3 domain repositories over it."""

    def __init__(
        self,
        conn,
        sectors: Repository[SectorAnalysis],
        sub_industries: Repository[SubIndustry],
        companies: Repository[CompanyPortfolio],
    ):
        self._conn = conn
        self.sectors = sectors
        self.sub_industries = sub_industries
        self.companies = companies

    @classmethod
    async def open(cls, db_path: str = "data/cache.db") -> "SqliteStorage":
        conn = await open_connection(db_path)
        return cls(
            conn,
            SqliteRepository(SqliteTable(conn, "sector"), SectorAnalysis, lambda s: s.sector),
            SqliteRepository(SqliteTable(conn, "sub_industry"), SubIndustry, lambda s: s.name),
            SqliteRepository(SqliteTable(conn, "company"), CompanyPortfolio, lambda c: c.name),
        )

    async def close(self) -> None:
        await self._conn.close()
