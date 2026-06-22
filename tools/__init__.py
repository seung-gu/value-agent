"""Shared agent tools, registered on agents that do web research.

Re-exported here so callers keep importing `from tools import get_today` regardless of
which module inside the package a tool lives in. Add new tools as `tools/<name>.py` and
re-export them below.
"""

from tools.dates import get_today

__all__ = ["get_today"]
