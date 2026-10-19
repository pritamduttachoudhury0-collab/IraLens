# -*- coding: utf-8 -*-
"""Unified web search — the `web-search` source.

Phase 1 moved the work into `iralens.search` (fallback chain, filters,
dedup, ranking, cache). This source keeps the original public contract:

  - `fetch("query", {"query", "limit", "backend"})` returns a list of `Artifact`s
    (what `IraLens.search()` has always returned).
  - `backend` still accepts the legacy names "semantic-search" and
    "browser-search" (the latter now means the DuckDuckGo -> Bing chain).
  - `fetch` raises SourceUnavailableError only when every engine failed.
    A query that is genuinely empty returns [] (documented in DECISIONS.md).

Use `IraLens.search_api()` for the full structured response.
"""

from __future__ import annotations

import shutil
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..errors import ExtractionError, SearchEnginesFailedError
from ..search import SearchEngine, to_artifacts
from ..search.engines import LEGACY_ALIASES
from ..search.schema import SearchFilters, SearchOptions
from ..settings import Settings
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context


#: Engine names the search chain may draw on, used to filter the circuit
#: snapshot down to the engines this source actually uses.
_SEARCH_ENGINE_KEYS = ("duckduckgo", "bing", "wikipedia", "startpage",
                       "brave", "mojeek", "marginalia", "gdelt", "semantic-search",
                       "crossref", "arxiv", "stackexchange", "github-search", "paid-backup")


def _engine_circuits() -> Dict[str, Dict[str, object]]:
    """Per-engine circuit state, or ``{}`` when it cannot be read.

    A local SQLite read of the breaker's own state file. It makes no upstream
    request, so `doctor` stays non-destructive, and it fails open: an
    unreadable store reports no circuit information rather than turning a
    storage problem into a health alarm.
    """
    try:
        from ..config import state_dir
        from ..reliability import PersistentCircuitBreaker
        from ..settings import Settings
        st = Settings()
        breaker = PersistentCircuitBreaker(
            st.breaker_failure_threshold,
            st.breaker_cooldown_seconds,
            blocked_cooldown=st.breaker_block_cooldown_seconds,
            state_path=state_dir() / "search-health.sqlite3",
        )
        raw = breaker.snapshot()
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, object]] = {}
    for name, state in raw.items():
        if isinstance(state, dict):
            out[str(name)] = {
                "state": state.get("state", "closed"),
                "consecutive_failures": state.get("consecutive_failures", 0),
            }
    return out


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
                "semantic search bridge present; web engines also available as fallback",
                self.active_backend,
                details={"direct_search": "configured",
                         "browser_fallback": "not_probed",
                         "provider_probe": "not_run"},
            )
        # Free engines fetch SERPs directly over SSRF-safe HTTP, so search
        # works with no browser engine installed. The engine, when present, is
        # a *rendered fallback* for bot walls and JS layouts — a secondary
        # capability, reported separately from direct-search readiness.
        self.active_backend = "direct-search"
        try:
            engine_health = context.engine().health()
        except Exception as exc:  # engine is optional; never fail health on it
            engine_health = {"status": "off", "error": str(exc)}
        engine_status = str(engine_health.get("status") or "").strip().lower()
        engine_message = str(engine_health.get("message") or "").strip()
        details: Dict[str, Any] = {
            "direct_search": "configured",
            "browser_fallback": engine_status or "unknown",
            # Doctor is non-destructive and makes no unsolicited upstream
            # request: whether DuckDuckGo/Bing are reachable right now is
            # genuinely untested, and the report says so.
            "provider_probe": "not_run",
        }
        if engine_message:
            details["browser_fallback_message"] = engine_message[:200]

        # Per-engine circuit state. Doctor used to say "ok" during a partial
        # outage: the breaker was open for one engine, another engine filled
        # the slots, and nothing in health reflected it. Reading the breaker
        # is a local SQLite read -- no upstream request, so doctor stays
        # non-destructive.
        circuits = _engine_circuits()
        if circuits:
            details["engine_circuits"] = circuits
            impaired = sorted(
                name for name, st in circuits.items()
                if str(st.get("state")) in ("open", "half_open")
            )
            if impaired:
                details["engines_impaired"] = impaired

        if engine_status == "off":
            tripped = details.get("engines_impaired") or []
            if tripped:
                return SourceHealth(
                    "warn",
                    "direct SERP fetch over HTTP; rendered browser fallback not "
                    "installed; " + f"{len(tripped)} search engine(s) are "
                    f"circuit-open ({', '.join(tripped)}), so coverage is "
                    "partial; upstream search providers not probed",
                    self.active_backend,
                    hint="an engine trips after repeated failures and recovers "
                         "after its cooldown; results carry a degradation "
                         "marker while this persists",
                    details=details,
                )
            return SourceHealth(
                "ok",
                "direct SERP fetch over HTTP; rendered browser fallback not "
                "installed; upstream search providers not probed",
                self.active_backend,
                details=details,
            )
        if engine_status == "error":
            # Never present a broken optional fallback as "all healthy": the
            # direct path is still usable, so the source is `warn`, not `ok`.
            reason = engine_message or "browser engine reported an error"
            return SourceHealth(
                "warn",
                "direct SERP fetch over HTTP is ready; rendered browser fallback "
                f"is NOT available ({reason}); upstream search providers not probed",
                self.active_backend,
                hint="run 'iralens doctor' for engine detail, or reinstall with "
                     "'iralens install-engine'",
                details=details,
            )
        if engine_status != "ok":
            # An unrecognized engine status is not evidence that the fallback
            # works: say it is unproven rather than claiming readiness.
            tripped = details.get("engines_impaired") or []
            if tripped:
                return SourceHealth(
                    "warn",
                    "direct SERP fetch over HTTP is ready; rendered browser "
                    f"fallback status unrecognized ({engine_status or 'empty'}); "
                    f"{len(tripped)} search engine(s) are circuit-open "
                    f"({', '.join(tripped)}), so coverage is partial; "
                    "upstream search providers not probed",
                    self.active_backend,
                    hint="an engine trips after repeated failures and recovers "
                         "after its cooldown",
                    details=details,
                )
            return SourceHealth(
                "ok",
                "direct SERP fetch over HTTP is ready; rendered browser fallback "
                f"status unrecognized ({engine_status or 'empty'}) and therefore "
                "unproven; upstream search providers not probed",
                self.active_backend,
                details=details,
            )
        self.active_backend = "browser-search"
        tripped = details.get("engines_impaired") or []
        if tripped:
            # The transport works; coverage does not. Reporting "ok" here is
            # what let a production integration keep serving filler results
            # while its monitoring showed green.
            return SourceHealth(
                "warn",
                "direct SERP fetch is ready, but "
                f"{len(tripped)} search engine(s) are circuit-open "
                f"({', '.join(tripped)}); results will be assembled from "
                "fewer sources and coverage is partial; upstream providers "
                "not probed",
                self.active_backend,
                hint="an engine trips after repeated failures and recovers "
                     "after its cooldown; results carry a degradation marker "
                     "while this persists",
                details=details,
            )
        return SourceHealth(
            "ok",
            "direct SERP fetch with rendered browser fallback available "
            "(engine probe passed); upstream search providers not probed",
            self.active_backend,
            details=details,
        )

    # --------------------------------------------------------------- fetch
    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op != "query":
            return super().fetch(op, params, context)
        query = (params.get("query") or "").strip()
        if not query:
            raise ExtractionError("search requires a non-empty 'query'")
        limit = int(params.get("limit") or 8)
        engine = self.search_engine(context)
        options = SearchOptions(
            max_results=limit,
            engines=self._legacy_engines(params.get("backend"), engine),
            offset=int(params.get("offset") or 0),
            exclude_urls=tuple(params.get("exclude_urls") or ()),
        )
        response = engine.search(query, filters=SearchFilters(), options=options, context=context)
        if response.no_results_reason == "engines_failed":
            raise SearchEnginesFailedError(
                "web search failed on every backend",
                hint="check engine status with: iralens doctor",
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
