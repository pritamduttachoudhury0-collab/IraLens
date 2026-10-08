# -*- coding: utf-8 -*-
"""Backend contract for the search subsystem.

A backend turns (query, filters, limit) into normalized `SearchHit`s, or raises
a HalfIraLensError subclass that the fallback layer classifies. Each backend
declares how it handles each filter:

  native      - the engine applies it (URL param or API argument)
  emulated    - applied through a query operator such as site: or filetype:
  post_filtered - applied to the returned hits by the engine layer (always
                  correct; may return fewer than `limit`)
  unsupported - not applied at all; reported to the caller, never silent

Backends never write to the cache and never read the user's session state.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from ...search.schema import SearchFilters, SearchHit

NATIVE = "native"
EMULATED = "emulated"
POST = "post_filtered"
UNSUPPORTED = "unsupported"


def active_filters(filters: SearchFilters) -> Dict[str, Any]:
    """Filter name -> value, for filters that are set."""
    out: Dict[str, Any] = {}
    if filters.date_from:
        out["date_from"] = filters.date_from
    if filters.date_to:
        out["date_to"] = filters.date_to
    if filters.include_domains:
        out["include_domains"] = list(filters.include_domains)
    if filters.exclude_domains:
        out["exclude_domains"] = list(filters.exclude_domains)
    if filters.file_type:
        out["file_type"] = filters.file_type
    if filters.language:
        out["language"] = filters.language
    if filters.region:
        out["region"] = filters.region
    return out


class SearchBackend(ABC):
    """One search engine. Subclasses set `name` and `handled` (filter -> mode)."""

    name: str = ""
    #: filter name -> mode, for filters this backend knows how to handle.
    handled: Dict[str, str] = {}

    def filter_modes(self, filters: SearchFilters) -> Dict[str, str]:
        modes: Dict[str, str] = {}
        for key in active_filters(filters):
            modes[key] = self.handled.get(key, UNSUPPORTED)
        return modes

    def build_query(self, query: str, filters: SearchFilters) -> str:
        """Query text sent to the engine (may include operators)."""
        return query

    @abstractmethod
    def search(self, query: str, filters: SearchFilters, limit: int, context: Any) -> List[SearchHit]:
        """Return normalized hits. Raise a HalfIraLensError on failure."""


def as_html(raw: Any) -> str:
    """Normalize engine.evaluate_js() output to a string of HTML."""
    if isinstance(raw, dict):
        for key in ("result", "value", "html"):
            if isinstance(raw.get(key), str):
                return raw[key]
        return json.dumps(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith('"') and text.endswith('"'):
            try:
                decoded = json.loads(text)
                if isinstance(decoded, str):
                    return decoded
            except json.JSONDecodeError:
                pass
        return raw
    return "" if raw is None else str(raw)


def fetch_rendered_html(context: Any, url: str, timeout: int) -> str:
    """Navigate the shared browser engine and return the rendered document HTML."""
    engine = context.engine()
    engine.navigate(url, wait_until="domcontentloaded", timeout=timeout)
    return as_html(engine.evaluate_js("document.documentElement.outerHTML"))
