"""Deps -- the research agents' dependency container (injected via pydantic-ai deps_type).

Carries the search client plus per-run SEARCH DISCIPLINE that keeps the agent out of the
reword-loop the logs showed:
- `max_calls`: a hard ceiling on web_search + web_read per run (a data-less market ends fast).
- `_queries` + `is_repeat`: reject a query that's a near-duplicate of one already tried, so a
  miss forces a new angle/source instead of the same query with tweaked wording.
- `GATED_DOMAINS`: paywalled aggregators to skip on read -- fetching them burns a call on a
  login teaser; prefer open primary sources (EIA, OPEC, company filings).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ports.search_client import SearchClient

# Paywalled / gated sources -- reading these wastes a fetch on a teaser behind a login.
GATED_DOMAINS = ("statista.com", "rystad", "woodmac", "spglobal.com", "gartner.com", "idc.com")


@dataclass
class Deps:
    """Agent dependencies: the search client + per-run search-discipline state."""

    search: SearchClient
    max_calls: int = 10
    _calls: int = field(default=0, init=False)
    _queries: list[str] = field(default_factory=list, init=False)

    def over_budget(self) -> bool:
        """Count one tool call; return True once the per-run ceiling is exceeded."""
        self._calls += 1
        return self._calls > self.max_calls

    def is_repeat(self, query: str) -> bool:
        """True if `query` near-duplicates one already tried (Jaccard token overlap > 0.6)."""
        toks = set(query.lower().split())
        if not toks:
            return False
        for prev in self._queries:
            ptoks = set(prev.lower().split())
            if ptoks and len(toks & ptoks) / len(toks | ptoks) > 0.6:
                return True
        return False

    def note_query(self, query: str) -> None:
        """Remember a query that was actually issued (for is_repeat checks)."""
        self._queries.append(query)
