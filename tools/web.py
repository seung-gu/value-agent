"""Web research tools -- agent-callable functions that delegate to the search adapter.

These are TOOLS (the LLM calls them); each delegates to the `SearchClient` adapter held in
`Deps.search`. They also enforce the per-run search discipline in Deps: a tool-call ceiling,
near-duplicate query rejection (no reword-looping), and skipping paywalled/gated domains on
read. Docstrings are generic; per-agent guidance lives in each agent's system prompt.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from agents.deps import GATED_DOMAINS, Deps

_LIMIT_MSG = (
    "TOOL-CALL LIMIT REACHED. Stop. If you already have a sourced table, finalize it; "
    "otherwise return EMPTY results. Do NOT estimate or pad."
)


async def web_search(ctx: RunContext[Deps], query: str) -> str:
    """Web search -- returns result snippets + URLs. Use this to FIND a relevant page.

    Keep queries SHORT (<=6 plain words, no quote operators); the answer is often already in
    the snippets. On a miss, change angle/source rather than rewording.
    """
    if ctx.deps.over_budget():
        return _LIMIT_MSG
    if ctx.deps.is_repeat(query):
        return (
            "This is almost the same as a search you already ran -- rewording will not help. "
            "Switch ANGLE or SOURCE: try an OPEN primary source (EIA, OPEC, the Energy "
            "Institute review, a company filing) or a genuinely different approach."
        )
    ctx.deps.note_query(query)
    ctx.deps.note_call()  # count only the real search (reword rejections above don't burn budget)
    return await ctx.deps.search.search(query)


async def web_read(ctx: RunContext[Deps], url: str) -> str:
    """Read a page's FULL content -- only when a snippet shows an OPEN page actually has the
    answer. Snippets often already hold the figure, so read sparingly.
    """
    if ctx.deps.over_budget():
        return _LIMIT_MSG
    if ctx.deps.already_read(url):
        return (
            f"You ALREADY read {url} earlier this run -- do NOT fetch it again (re-reading a "
            "big PDF wastes time). Use what you got from it, or read a DIFFERENT source."
        )
    if any(g in url.lower() for g in GATED_DOMAINS):
        return (
            f"{url} is a paywalled/gated source -- skip it (a fetch here just burns a call on "
            "a login teaser). Take the figure from the search SNIPPETS, or read an OPEN "
            "source (EIA, OPEC, a company filing)."
        )
    ctx.deps.note_call()  # count only the real fetch (gated/duplicate skips above don't burn budget)
    ctx.deps.note_read(url)
    text = await ctx.deps.search.scrape(url)
    return text[:8000]  # cap to keep token cost bounded
