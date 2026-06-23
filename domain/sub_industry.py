"""Sub-industry domain -- the analysis unit below an industry group.

`SubIndustry` is the STATIC definition (curated once by the sub_industry_agent + human
review, then fixed). `SubIndustryMetric` is its quarterly time-series (cagr, penetration),
accumulated one row per period -- kept separate so the definition never drifts per quarter.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubIndustry(BaseModel):
    """A sub-industry under an industry group (e.g. 'Foundry') -- static definition."""

    sub_code: str          # surrogate, e.g. "4530-01"
    group_code: str        # FK -> GicsReference.group_code
    name: str              # "Foundry"
    definition: str = ""   # short scope description


class SubIndustryMetric(BaseModel):
    """A sub-industry's metrics for one period (time-series row)."""

    sub_code: str                                                  # FK -> SubIndustry.sub_code
    period: str                                                    # freshness bucket, "2026-Q2"
    cagr: float | None = None                                      # %, source-backed (may be < 0)
    penetration: float | None = Field(default=None, ge=0, le=100)  # %
    source: str = ""                                               # source URL for the figures
