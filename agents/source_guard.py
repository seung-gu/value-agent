"""Shared validator helper: every `source` must EXACTLY match a page the agent web_read.

Closes the 'source laundering' gap -- the agent reads page A (e.g. a real trendforce article
at .../20260612-13095.html, HTTP 200) but cites a truncated/guessed URL B (.../202606, HTTP
404) it never actually opened. We collect the exact URLs passed to `web_read` and require each
cited source to match one of them by host+path -- so a source can only be a page really read.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse


def _norm(url: str) -> str:
    """Host+path of a URL, normalized ('https://www.x.com/a/' -> 'x.com/a'); '' if not a URL.

    Scheme, query, fragment, trailing slash and a leading 'www.' are ignored so trivially
    different spellings of the SAME page still match -- but a different path does not.
    """
    p = urlparse(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}" if host else ""


def _tool_url(part) -> str:
    """The `url` argument of a web_read tool-call part, robust to dict-or-JSON-string args."""
    try:
        d = part.args_as_dict()
    except Exception:
        raw = getattr(part, "args", None)
        if isinstance(raw, dict):
            d = raw
        elif isinstance(raw, str):
            try:
                d = json.loads(raw)
            except Exception:
                d = {}
        else:
            d = {}
    return d.get("url", "") if isinstance(d, dict) else ""


def read_urls(messages) -> list[str]:
    """The exact URLs the agent passed to web_read across the run (in order, deduped)."""
    seen: set[str] = set()
    out: list[str] = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            is_call = getattr(part, "part_kind", None) == "tool-call"
            if is_call and getattr(part, "tool_name", "") == "web_read":
                url = _tool_url(part).strip()
                if url and url not in seen:
                    seen.add(url)
                    out.append(url)
    return out


def unread_sources(sources: list[str], read: list[str]) -> list[str]:
    """Sources whose host+path matches no web_read page (blank / non-URL sources ignored)."""
    read_norm = {_norm(u) for u in read}
    read_norm.discard("")
    bad: list[str] = []
    for src in sources:
        n = _norm(src)
        if n and n not in read_norm:
            bad.append(src)
    return bad
