"""SQLite adapters -- the 6 domain repositories over one shared connection.

`SqliteStorage.open(db_path)` opens the connection (+ creates tables) and exposes:
  static:      .gics  .sub_industries  .companies
  time-series: .metrics  .market_shares  .portfolios
Close once via `close()`. The (table, pk/parent) wiring is hardcoded here.
"""

from __future__ import annotations

import aiosqlite

from adapters.sqlite.base import open_connection
from adapters.sqlite.repository import StaticTable, TimeSeriesTable
from domain import (
    Company,
    CompanyPortfolio,
    GicsReference,
    MarketShare,
    SubIndustry,
    SubIndustryMetric,
)
from ports.repository import StaticRepository, TimeSeriesRepository

__all__ = ["SqliteStorage", "StaticTable", "TimeSeriesTable"]


class SqliteStorage:
    """Owns one sqlite connection and exposes the 6 domain repositories over it."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        *,
        gics: StaticRepository[GicsReference],
        sub_industries: StaticRepository[SubIndustry],
        companies: StaticRepository[Company],
        metrics: TimeSeriesRepository[SubIndustryMetric],
        market_shares: TimeSeriesRepository[MarketShare],
        portfolios: TimeSeriesRepository[CompanyPortfolio],
    ):
        self._conn = conn
        # static
        self.gics = gics
        self.sub_industries = sub_industries
        self.companies = companies
        # time-series
        self.metrics = metrics
        self.market_shares = market_shares
        self.portfolios = portfolios

    @classmethod
    async def open(cls, db_path: str = "data/cache.db") -> SqliteStorage:
        conn = await open_connection(db_path)
        return cls(
            conn,
            gics=StaticTable(conn, "gics_reference", GicsReference, "group_code"),
            sub_industries=StaticTable(conn, "sub_industry", SubIndustry, "sub_code"),
            companies=StaticTable(conn, "company", Company, "company_code"),
            metrics=TimeSeriesTable(conn, "sub_industry_metric", SubIndustryMetric, "sub_code"),
            market_shares=TimeSeriesTable(conn, "market_share", MarketShare, "sub_code"),
            portfolios=TimeSeriesTable(conn, "company_portfolio", CompanyPortfolio, "company_code"),
        )

    async def close(self) -> None:
        await self._conn.close()
