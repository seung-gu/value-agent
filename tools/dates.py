"""Date/time tools for agents -- anchor research on the real current date."""

from __future__ import annotations

from datetime import datetime, timezone


def get_today() -> str:
    """Return today's date + quarter so an agent anchors on the REAL current date, not 2024.

    Deliberately gives ONLY the date -- no multi-quarter "research window". The window read as
    "go scrape every quarter in that 2-year range" and blew up the search count; the agent
    should just take the single most recent reported figure.
    """
    now = datetime.now(timezone.utc)
    q = (now.month - 1) // 3 + 1
    return f"Today is {now:%Y-%m-%d} (Q{q} {now.year}). Use the single latest reported figure available."
