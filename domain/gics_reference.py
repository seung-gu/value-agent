"""GICS reference domain -- a fixed industry-group seed (read-only).

One row per GICS industry group (4-digit). The parent sector (2-digit) is folded in as
columns rather than its own table -- 25 rows make the duplication harmless. Seeded from
GICS once; never written by agents.
"""

from __future__ import annotations

from pydantic import BaseModel


class GicsReference(BaseModel):
    """A GICS industry group (4-digit) with its parent sector -- fixed reference."""

    group_code: str    # "4530"
    sector_code: str   # "45"
    sector_name: str   # "Information Technology"
    group_name: str    # "Semiconductors & Semiconductor Equipment"
