"""GICS industry-group seed -- the 25 fixed groups (read-only reference).

Sourced from the GICS 2023 structure (the same data tabulated in gics.md). The 25 industry
groups are a stable standard, so they're listed here explicitly (parsing the markdown would
be fragile). `seed_gics` upserts them idempotently on boot; agents never write gics_reference.
"""

from __future__ import annotations

from domain import GicsReference
from ports.repository import StaticRepository

# (sector_code, sector_name, group_code, group_name) -- 25 GICS industry groups.
_GROUPS: list[tuple[str, str, str, str]] = [
    ("10", "Energy", "1010", "Energy"),
    ("15", "Materials", "1510", "Materials"),
    ("20", "Industrials", "2010", "Capital Goods"),
    ("20", "Industrials", "2020", "Commercial & Professional Services"),
    ("20", "Industrials", "2030", "Transportation"),
    ("25", "Consumer Discretionary", "2510", "Automobiles & Components"),
    ("25", "Consumer Discretionary", "2520", "Consumer Durables & Apparel"),
    ("25", "Consumer Discretionary", "2530", "Consumer Services"),
    ("25", "Consumer Discretionary", "2550", "Consumer Discretionary Distribution & Retail"),
    ("30", "Consumer Staples", "3010", "Consumer Staples Distribution & Retail"),
    ("30", "Consumer Staples", "3020", "Food, Beverage & Tobacco"),
    ("30", "Consumer Staples", "3030", "Household & Personal Products"),
    ("35", "Health Care", "3510", "Health Care Equipment & Services"),
    ("35", "Health Care", "3520", "Pharmaceuticals, Biotechnology & Life Sciences"),
    ("40", "Financials", "4010", "Banks"),
    ("40", "Financials", "4020", "Financial Services"),
    ("40", "Financials", "4030", "Insurance"),
    ("45", "Information Technology", "4510", "Software & Services"),
    ("45", "Information Technology", "4520", "Technology Hardware & Equipment"),
    ("45", "Information Technology", "4530", "Semiconductors & Semiconductor Equipment"),
    ("50", "Communication Services", "5010", "Telecommunication Services"),
    ("50", "Communication Services", "5020", "Media & Entertainment"),
    ("55", "Utilities", "5510", "Utilities"),
    ("60", "Real Estate", "6010", "Equity Real Estate Investment Trusts (REITs)"),
    ("60", "Real Estate", "6020", "Real Estate Management & Development"),
]

# Built at import -- pydantic validates the 25 rows up front.
GICS_GROUPS: list[GicsReference] = [
    GicsReference(sector_code=s, sector_name=sn, group_code=g, group_name=gn)
    for (s, sn, g, gn) in _GROUPS
]


async def seed_gics(gics: StaticRepository[GicsReference]) -> int:
    """Idempotently upsert the 25 GICS industry groups. Returns the row count."""
    for group in GICS_GROUPS:
        await gics.upsert(group)
    return len(GICS_GROUPS)
