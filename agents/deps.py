"""Deps -- the research agents' dependency container (injected via pydantic-ai deps_type)."""

from __future__ import annotations

from dataclasses import dataclass

from ports.search_client import SearchClient


@dataclass
class Deps:
    """Agent dependencies: the search client (Serper, or any SearchClient implementation)."""

    search: SearchClient
