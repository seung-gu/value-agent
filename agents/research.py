"""Factory for the project's research agents -- the ONE place the shared wiring lives.

Every research agent needs the same things: the `Deps` search-discipline, the web tools
(get_today / web_search / web_read), and a today-anchor system prompt so the model searches the
current year instead of its 2024 training cutoff. Re-registering those in each agent file means
forgetting one eventually (which is exactly how sub_industry_agent ended up searching 2024).

Build an agent with `research_agent(model, OutputType, instructions=...)` and it gets all of the
above automatically; attach domain-specific output_validators on the returned agent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic_ai import Agent

from agents.deps import Deps
from tools import get_today
from tools.web import web_read, web_search


def today_note() -> str:
    """Today's date as a system-prompt line so models search the current year, not their 2024 cutoff."""
    now = datetime.now(timezone.utc)
    return (
        f"Today is {now:%Y-%m-%d}; the current year is {now.year}. Anchor on it: search for "
        f"{now.year} or {now.year - 1} data and read the reporting period off the source -- "
        "NEVER default to an older year like 2024 unless explicitly asked."
    )


def research_agent(model: str, output_type, *, instructions: str, retries: int = 4) -> Agent:
    """A pydantic-ai Agent pre-wired for web research: Deps + the web tools + the today-anchor.

    Domain specifics (extra @system_prompt, @output_validator) are attached by the caller on the
    returned agent. Keeping the common wiring here means a NEW research agent can't silently miss
    the date note or a web tool.
    """
    agent = Agent(
        model,
        deps_type=Deps,
        output_type=output_type,
        retries=retries,
        system_prompt=instructions,
    )
    agent.system_prompt(today_note)   # date anchor injected on every run
    agent.tool_plain(get_today)
    agent.tool(web_search)
    agent.tool(web_read)
    return agent
