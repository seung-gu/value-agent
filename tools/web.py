"""Web research tools -- agent-callable functions that delegate to the search adapter.

These are TOOLS (the LLM calls them); each delegates to the `SearchClient` adapter held in
`Deps.search` (Deps lives in `agents/deps.py`). Shared by every research agent and registered
via `agent.tool(web_search)`, so they aren't redefined per agent. Docstrings are intentionally
generic; per-agent usage guidance lives in each agent's system prompt.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from agents.deps import Deps


async def web_search(ctx: RunContext[Deps], query: str) -> str:
    """Web search -- returns result snippets + URLs. Use this to FIND a relevant page."""
    return await ctx.deps.search.search(query)


async def web_read(ctx: RunContext[Deps], url: str) -> str:
    """Read a page's FULL content (tables, full text) -- use after web_search on a good URL.

    Search snippets do NOT contain tables/full figures; this reads the actual page so you
    pull the real numbers instead of guessing or re-searching.
    """
    text = await ctx.deps.search.scrape(url)
    return text[:8000]  # cap to keep token cost bounded
