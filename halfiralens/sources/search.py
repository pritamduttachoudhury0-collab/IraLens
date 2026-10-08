# -*- coding: utf-8 -*-
"""Unified web search — the `web-search` source.

Phase 1 moved the work into `halfiralens.search` (fallback chain, filters,
dedup, ranking, cache). This source keeps the original public contract:

  - `fetch("query", {"query", "limit", "backend"})` returns a list of `Artifact`s
    (what `HalfIraLens.search()` has always returned).
  - `backend` still accepts the legacy names "semantic-search" and
    "browser-search" (the latter now means the DuckDuckGo -> Bing chain).
  - `fetch` raises SourceUnavailableError only when every engine failed.
    A query that is genuinely empty returns [] (documented in DECISIONS.md).

Use `HalfIraLens.search_api()` for the full structured response.
"""

from __future__ import annotations

import shutil
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..errors import ExtractionError, SourceUnavailableError
from ..search import SearchEngine, to_artifacts
from ..search.engines import LEGACY_ALIASES
from ..search.schema import SearchFilters, SearchOptions
from ..settings import Settings
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context


class SearchSource(Source):
    name = "web-search"
    description = "Web search"
    backends = ["semantic-search", "browser-search"]
    tier = 0
    operations = {
        "query": {
            "description": "Search the open web and return ranked results",
            "params": {"query": "search text", "limit": "max results (default 8)",
                       "backend": "optional engine name or legacy alias"},
        }
    }

    def __init__(self) -> None:
        super().__init__()
        self._engine: Optional[SearchEngine] = None
        self._engine_lock = threading.Lock()

    # ------------------------------------------------------------ wiring
    def search_engine(self, context: "Context") -> SearchEngine:
        """One SearchEngine per source instance, built from the current config."""
        with self._engine_lock:
            if self._engine is None:
                settings = Settings.from_config(context.config)
                self._engine = SearchEngine(settings)
            return self._engine

    def health(self, context: "Context") -> SourceHealth:
        if shutil.which("mcporter"):
            self.active_backend = "semantic-search"
            return SourceHealth(
                "ok",
                "semantic search bridge present; browser search also available as fallback",
                self.active_backend,
            )
        self.active_backend = "browser-search"
        engine_health = context.engine().health()
        if engine_health.get("status") == "off":
            self.active_backend = None
            return SourceHealth(
                "off",
                "no search backend: install the browser engine (halfiralens install-engine) "
                "or configure the semantic-search bridge (npm i -g mcporter && "
                "mcporter config add exa https://mcp.exa.ai/mcp --scope home)",
            )
        return SourceHealth("ok", "browser-driven search engine", self.active_backend)

    # --------------------------------------------------------------- fetch
    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op != "query":
            return super().fetch(op, params, context)
        query = (params.get("query") or "").strip()
        if not query:
            raise ExtractionError("search requires a non-empty 'query'")
        limit = int(params.get("limit") or 8)
        engine = self.search_engine(context)
        response = engine.search(
            query,
            filters=SearchFilters(),
            options=SearchOptions(max_results=limit, engines=self._legacy_engines(params.get("backend"), engine)),
            context=context,
        )
        if response.no_results_reason == "engines_failed":
            raise SourceUnavailableError(
                "web search failed on every backend",
                hint="check engine status with: halfiralens doctor",
                detail="; ".join(f"{o.engine}: {o.kind}" for o in response.outcomes),
            )
        return to_artifacts(response, query)

    @staticmethod
    def _legacy_engines(preferred: Any, engine: SearchEngine) -> tuple:
        """Map the legacy `backend` argument to an engine list (empty = default chain)."""
        name = str(preferred or "").strip()
        if not name:
            return ()
        if name in LEGACY_ALIASES:
            return tuple(LEGACY_ALIASES[name])
        matches = tuple(b for b in engine.backends if b.startswith(name))
        return matches or ()
