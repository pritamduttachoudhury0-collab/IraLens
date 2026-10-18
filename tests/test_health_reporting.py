# -*- coding: utf-8 -*-
"""F-04: search health must be decomposable and must never over-claim.

The defect: `SearchSource.health()` special-cased only the browser status
`off`. Any other status -- including `error` -- fell through to an `ok`
response whose message claimed "direct SERP fetch with rendered browser
fallback". A broken optional fallback was therefore reported as readiness,
and the message implied upstream providers had been checked when they had
not.

These tests inject `off` / `ok` / `error` browser health and assert that the
reported status, message and structured `details` all differ accordingly.
"""

from __future__ import annotations

from typing import Any, Dict, List

from iralens.search import SearchEngine, to_artifacts
from iralens.search.engines.base import SearchBackend
from iralens.settings import Settings
from iralens.sources.search import SearchSource


class _FakeEngine:
    def __init__(self, health: Dict[str, Any]) -> None:
        self._health = health
        self.probed = False

    def health(self) -> Dict[str, Any]:
        self.probed = True
        if isinstance(self._health, Exception):
            raise self._health
        return self._health


class _EngineRaising:
    """A context whose `engine()` accessor itself fails."""

    class _Config:
        def get(self, key, default=None):
            return default

    def __init__(self) -> None:
        self.config = self._Config()

    def engine(self):
        raise RuntimeError("engine subsystem unavailable")


class _FakeContext:
    class _Config:
        def get(self, key, default=None):
            return default

    def __init__(self, engine_health: Any) -> None:
        self.config = self._Config()
        self.engine_handle = _FakeEngine(engine_health)

    def engine(self):
        return self.engine_handle


def _health(engine_health: Any) -> Any:
    source = SearchSource()
    return source.health(_FakeContext(engine_health))


# ------------------------------------------------------------------- browser ok
def test_browser_ok_reports_both_capabilities_and_an_unprobed_provider():
    result = _health({"status": "ok", "message": "browser engine ready"})
    assert result.status == "ok"
    assert result.active_backend == "browser-search"
    assert "rendered browser fallback" in result.message
    assert "not probed" in result.message
    assert result.details["direct_search"] == "configured"
    assert result.details["browser_fallback"] == "ok"
    assert result.details["provider_probe"] == "not_run"


# ------------------------------------------------------------------ browser off
def test_browser_off_keeps_direct_search_usable():
    result = _health({"status": "off", "message": "not installed"})
    assert result.status == "ok"
    assert result.active_backend == "direct-search"
    assert "not installed" in result.message
    assert "not probed" in result.message
    assert result.details["browser_fallback"] == "off"
    assert result.details["provider_probe"] == "not_run"


# --------------------------------------------------------------- browser error
def test_browser_error_never_claims_a_rendered_fallback():
    """The acceptance criterion: no ok/warn response may claim a fallback."""
    result = _health({"status": "error", "message": "cannot execute"})
    assert result.status != "ok", "a broken fallback must not be reported as ok"
    assert result.status == "warn"
    assert result.active_backend == "direct-search"
    assert "NOT available" in result.message
    assert "cannot execute" in result.message
    assert result.details["browser_fallback"] == "error"
    assert result.details["direct_search"] == "configured"
    # The direct path is still usable, and still reported separately.
    assert "direct SERP fetch over HTTP is ready" in result.message
    assert result.hint


def test_unknown_browser_status_is_not_treated_as_readiness():
    result = _health({"status": "degraded"})
    assert "unrecognized" in result.message
    assert result.details["browser_fallback"] == "degraded"
    assert "rendered browser fallback available" not in result.message


def test_missing_status_field_is_reported_not_assumed():
    result = _health({})
    assert result.details["browser_fallback"] in ("unknown", "")
    assert "rendered browser fallback available" not in result.message


# --------------------------------------------------- engine accessor explosions
def test_engine_accessor_failure_is_contained():
    """A failing optional subsystem must not fail the health report."""
    source = SearchSource()
    result = source.health(_EngineRaising())
    assert result.status == "ok"
    assert result.active_backend == "direct-search"
    assert result.details["browser_fallback"] == "off"
    assert "not installed" in result.message


def test_engine_health_raising_is_contained():
    result = _health(RuntimeError("probe exploded"))
    assert result.status == "ok"
    assert result.details["browser_fallback"] == "off"


# ------------------------------------------------------------ serialisation
def test_details_survive_doctor_serialisation():
    payload = _health({"status": "error", "message": "cannot execute"}).to_dict()
    assert payload["status"] == "warn"
    assert payload["details"]["browser_fallback"] == "error"
    assert payload["details"]["provider_probe"] == "not_run"


def test_details_are_omitted_when_empty():
    from iralens.sources.base import SourceHealth

    assert "details" not in SourceHealth("ok", "fine").to_dict()


# --------------------------------------------- direct search works, browser off
class _StubBackend(SearchBackend):
    """A backend that answers locally: a direct SERP fetch, no browser."""

    handled: Dict[str, Any] = {}

    def __init__(self) -> None:
        self.name = "duckduckgo"
        self.calls: List[str] = []

    def search(self, query, filters, limit, context):
        from iralens.search.schema import SearchHit

        self.calls.append(query)
        return [SearchHit(title="Direct result", url="https://example.org/a",
                          snippet="Found directly over HTTP.", engine="duckduckgo",
                          position=1, query=query)]


def test_direct_search_succeeds_while_the_browser_fallback_is_off(tmp_path, monkeypatch):
    """The two capabilities really are independent: search needs no engine."""
    from iralens.cache import ResponseCache
    from iralens.reliability import ConcurrencyGate
    from iralens.search.schema import SearchFilters, SearchOptions

    backend = _StubBackend()
    settings = Settings(search_engines=("duckduckgo",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, reformulate_max_queries=1)
    engine = SearchEngine(settings, backends={"duckduckgo": backend},
                          cache=ResponseCache(tmp_path / "c"),
                          gate=ConcurrencyGate(1, 1.0))

    source = SearchSource()
    source._engine = engine
    context = _FakeContext({"status": "off", "message": "not installed"})

    result = source.health(context)
    assert result.status == "ok"
    assert result.details["browser_fallback"] == "off"
    assert result.details["provider_probe"] == "not_run"

    response = engine.search(
        "direct only", filters=SearchFilters(),
        options=SearchOptions(max_results=5, engines=("duckduckgo",), reformulate=False),
        context=context)
    assert response.results
    artifacts = to_artifacts(response, "direct only")
    assert artifacts and artifacts[0].title == "Direct result"
    # No browser was consulted for any part of that search.
    assert backend.calls


def test_semantic_bridge_short_circuit_is_still_reported(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/mcporter"
                        if name == "mcporter" else None)
    source = SearchSource()
    result = source.health(_FakeContext({"status": "ok"}))
    assert result.active_backend == "semantic-search"
    assert "semantic search bridge present" in result.message
    assert result.details["provider_probe"] == "not_run"


# --------------------------------------------------------- doctor integration
def test_doctor_sources_block_carries_the_details(tmp_path):
    from iralens import IraLens
    from iralens.config import Config

    with IraLens(Config(tmp_path / "config.yaml")) as hil:
        report = hil.doctor()
    entry = report["sources"]["web-search"]
    assert "provider_probe" in entry.get("details", {})
    assert entry["details"]["provider_probe"] == "not_run"
    assert "not probed" in entry["message"]
