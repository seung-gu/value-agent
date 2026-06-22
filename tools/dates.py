"""Date/time tools for agents -- anchor research on the real current date."""

from __future__ import annotations

from datetime import datetime, timezone


def get_today() -> str:
    """Return today's date, current quarter, and the recommended research window (current quarter +/-4Q).

    Registered as a tool_plain so an agent anchors on the REAL current date and searches
    for the latest data instead of its training-cutoff year.
    """
    now = datetime.now(timezone.utc)
    q = (now.month - 1) // 3 + 1

    def shift_quarter(year: int, quarter: int, delta: int) -> tuple[int, int]:
        idx = year * 4 + (quarter - 1) + delta
        return idx // 4, idx % 4 + 1

    start_y, start_q = shift_quarter(now.year, q, -4)
    end_y, end_q = shift_quarter(now.year, q, 4)
    return (
        f"Today is {now:%Y-%m-%d}, which is Q{q} {now.year}. "
        f"Prioritize the most recent data. "
        f"Research window: Q{start_q} {start_y} to Q{end_q} {end_y} "
        f"(current quarter +/-4 quarters)."
    )
