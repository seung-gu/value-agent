"""Deps -- the research agents' dependency container (injected via pydantic-ai deps_type).

Carries the search client plus per-run SEARCH DISCIPLINE that keeps the agent out of the
reword-loop the logs showed:
- `max_calls`: a backstop on the count of ACTUAL web_search + web_read per run. Rejected
  reword/gated probes do NOT count (see `note_call`), so the ceiling bounds genuine research
  rather than getting eaten by failed attempts -- a runaway still can't spin forever.
- `_queries` + `is_repeat`: reject a query that's a near-duplicate of one already tried, so a
  miss forces a new angle/source instead of the same query with tweaked wording (this, not the
  ceiling, is what actually stops the reword-loop that burned 600 credits).
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
    max_calls: int = 25
    _calls: int = field(default=0, init=False)
    _queries: list[str] = field(default_factory=list, init=False)

    def over_budget(self) -> bool:
        """True once the ceiling of ACTUAL search/scrape calls is reached (does NOT increment).

        Only real network calls count (via `note_call`); a rejected reword or a gated-domain
        skip never burns budget, so the ceiling bounds genuine research, not failed probes.
        """
        return self._calls >= self.max_calls

    def note_call(self) -> None:
        """Count one ACTUAL search/scrape -- call right before the network hit."""
        self._calls += 1

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
