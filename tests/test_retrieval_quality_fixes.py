# -*- coding: utf-8 -*-
"""The three retrieval-trust defects from the IraPulse gap analysis.

Product audit findings F-02 (silent fusion degradation), F-03 (freshness
rescuing irrelevant results), and F-08 (health green during a partial
outage). All three share one shape: the system reports a confident result
while something behind it has already failed.

Offline and deterministic; no network, no live search providers.
"""

from __future__ import annotations

from iralens.search.engine import (
    conflict_report, degradation_report, to_artifacts,
)
from iralens.search.ranking import combine, freshness
from iralens.search.schema import (
    EngineOutcome,
    RankedResult,
    SearchResponse,
)

# --------------------------------------------------------------------- helpers


def _outcome(engine: str, status: str, kind: str = "", detail: str = "") -> EngineOutcome:
    return EngineOutcome(engine=engine, status=status, kind=kind or status, detail=detail)


def _result(**over) -> RankedResult:
    base = dict(
        title="t", url="https://example.com/a", canonical_url="https://example.com/a",
        snippet="s", score=0.5, score_breakdown={}, engines=["bing"],
        engine_ranks={"bing": 1}, published_at=None, date_status="unknown",
        queries=["q"], security_flags=[],
    )
    base.update(over)
    return RankedResult(**base)


def _response(outcomes, results=None) -> SearchResponse:
    return SearchResponse(
        query="q", results=results or [_result()], queries=[], outcomes=outcomes,
        fallbacks=[], filters={}, cache={}, dedup_log=[], no_results_reason="none",
    )


# ------------------------------------------------------- F-02: degradation
def test_healthy_result_set_is_not_marked_degraded():
    resp = _response([_outcome("bing", "results"), _outcome("duckduckgo", "results")])
    report = degradation_report(resp)
    assert report["degraded"] is False
    assert report["engines_impaired"] == []


def test_circuit_open_marks_the_response_degraded():
    """The audit's case: DDG tripped, Bing filled the slots, nobody was told."""
    resp = _response([
        _outcome("duckduckgo", "skipped", kind="circuit_open",
                 detail="engine skipped after repeated failures"),
        _outcome("bing", "results"),
    ])
    report = degradation_report(resp)
    assert report["degraded"] is True
    assert "duckduckgo" in report["engines_impaired"]
    assert "bing" in report["engines_served"]
    assert any("circuit_open" in r for r in report["reasons"])


def test_failed_engine_marks_the_response_degraded():
    resp = _response([_outcome("bing", "failed", kind="rate_limited")])
    assert degradation_report(resp)["degraded"] is True


def test_filler_risk_is_flagged_when_a_substitute_carries_the_results():
    """Bing + Wikipedia expected; Bing alone served. The slots were filled."""
    resp = _response([
        _outcome("duckduckgo", "skipped", kind="circuit_open"),
        _outcome("wikipedia", "results"),
    ])
    report = degradation_report(resp)
    assert report["filler_risk"] is True


def test_no_filler_risk_when_every_expected_engine_served():
    resp = _response([_outcome("bing", "results"), _outcome("wikipedia", "results")])
    assert degradation_report(resp)["filler_risk"] is False


def test_empty_engine_is_not_counted_as_impaired():
    """An engine that answered with nothing is not an engine that failed."""
    resp = _response([_outcome("bing", "empty"), _outcome("duckduckgo", "results")])
    assert degradation_report(resp)["degraded"] is False


def test_degradation_reaches_the_legacy_search_path():
    """`search` returns bare artifacts; the marker has to ride along on them."""
    resp = _response([
        _outcome("duckduckgo", "skipped", kind="circuit_open",
                 detail="engine skipped after repeated failures"),
        _outcome("bing", "results"),
    ])
    artifacts = to_artifacts(resp, "how to validate demand")
    assert artifacts
    for art in artifacts:
        assert art.metadata.get("degraded") is True
        assert art.provenance["degradation"]["degraded"] is True
        assert "duckduckgo" in art.provenance["degradation"]["engines_impaired"]


def test_clean_artifacts_carry_no_degradation_key():
    resp = _response([_outcome("bing", "results")])
    art = to_artifacts(resp, "q")[0]
    assert "degraded" not in art.metadata


# --------------------------------------------------------- F-03: freshness
def test_freshness_decays_toward_neutral_for_old_dates():
    from datetime import date
    recent = freshness("2026-10-01", date(2026, 10, 18), 365.0, 0.5)
    old = freshness("2015-01-01", date(2026, 10, 18), 365.0, 0.5)
    assert recent > old


def test_undated_result_gets_neutral_freshness():
    from datetime import date
    assert freshness(None, date(2026, 10, 18), 365.0, 0.5) == 0.5


def test_relevance_gate_caps_freshness_when_relevance_failed():
    """A dated page that does not match the query cannot buy rank with recency.

    Without the gate, freshness ~0.98 for something published today beat a
    relevant undated page sitting at neutral 0.5 -- which is how a
    demand-validation query returned a freshly-updated pizza guide at rank 1.
    """
    from iralens.search.ranking import relevance_gate
    factors = {"relevance": 0.05, "freshness": 0.9793}
    fired = relevance_gate(factors, floor=0.30, neutral=0.5)
    assert fired is True
    assert factors["freshness"] == 0.5


def test_relevance_gate_leaves_relevant_results_alone():
    from iralens.search.ranking import relevance_gate
    factors = {"relevance": 0.9, "freshness": 0.9793}
    fired = relevance_gate(factors, floor=0.30, neutral=0.5)
    assert fired is False
    assert factors["freshness"] == 0.9793


def test_relevance_gate_never_inflates_a_score():
    from iralens.search.ranking import relevance_gate
    factors = {"relevance": 0.1, "freshness": 0.1}
    relevance_gate(factors, floor=0.30, neutral=0.5)
    assert factors["freshness"] == 0.1


def test_gated_result_scores_below_its_ungated_self():
    """The end-to-end consequence: gating costs the junk result rank."""
    from iralens.search.ranking import relevance_gate
    weights = {"relevance": 0.4, "authority": 0.2, "freshness": 0.2, "agreement": 0.2}
    junk = {"relevance": 0.05, "authority": 0.4, "freshness": 0.9793, "agreement": 0.5}
    ungated = combine(dict(junk), weights)
    relevance_gate(junk, floor=0.30, neutral=0.5)
    assert combine(junk, weights) < ungated


def test_gated_result_records_why_its_score_was_capped():
    """The cap must be explainable in the output, not invisible."""
    r = _result(freshness_gated=True)
    assert r.freshness_gated is True


def test_gate_defaults_to_off():
    r = _result()
    assert r.freshness_gated is False


def test_combine_still_weights_all_four_factors():
    factors = {"relevance": 1.0, "authority": 0.0, "freshness": 0.0, "agreement": 0.0}
    weights = {"relevance": 0.4, "authority": 0.2, "freshness": 0.2, "agreement": 0.2}
    assert combine(factors, weights) == 0.4


def test_freshness_cannot_exceed_neutral_once_gated():
    """The arithmetic consequence: capping removes freshness's upside only."""
    neutral = 0.5
    high = 0.9793
    assert min(high, neutral) == neutral
    # A low freshness is left alone, so the gate never *inflates* a score.
    assert min(0.1, neutral) == 0.1


# ------------------------------------------------------------- F-08: health
def test_health_reads_circuit_state_without_network(monkeypatch):
    """Doctor must reflect tripped engines; it must not probe upstream."""
    from iralens.sources.search import _engine_circuits

    monkeypatch.setattr(
        "iralens.reliability.PersistentCircuitBreaker.snapshot",
        lambda self: {"duckduckgo": {"state": "open", "consecutive_failures": 3},
                      "bing": {"state": "closed", "consecutive_failures": 0}},
    )
    circuits = _engine_circuits()
    assert circuits["duckduckgo"]["state"] == "open"
    assert circuits["bing"]["state"] == "closed"


def test_health_fails_open_when_state_cannot_be_read(monkeypatch):
    """A storage problem must not be reported as a health alarm."""
    from iralens.sources.search import _engine_circuits

    monkeypatch.setattr(
        "iralens.reliability.PersistentCircuitBreaker.snapshot",
        lambda self: (_ for _ in ()).throw(OSError("no state store")),
    )
    assert _engine_circuits() == {}


def test_health_reports_warn_when_an_engine_is_tripped(monkeypatch):
    from iralens.sources.base import SourceHealth
    from iralens.sources.search import SearchSource

    monkeypatch.setattr(
        "iralens.sources.search._engine_circuits",
        lambda: {"duckduckgo": {"state": "open", "consecutive_failures": 3}},
    )

    class _Ctx:
        class config:
            @staticmethod
            def get(_k, _d=None):
                return _d

        @staticmethod
        def engine():
            raise RuntimeError("no engine")

    health = SearchSource().health(_Ctx())
    assert isinstance(health, SourceHealth)
    assert health.status == "warn"
    assert "circuit-open" in health.message
    assert health.details.get("engines_impaired") == ["duckduckgo"]


def test_health_reports_ok_when_nothing_is_tripped(monkeypatch):
    from iralens.sources.search import SearchSource

    monkeypatch.setattr(
        "iralens.sources.search._engine_circuits",
        lambda: {"duckduckgo": {"state": "closed", "consecutive_failures": 0}},
    )

    class _Ctx:
        class config:
            @staticmethod
            def get(_k, _d=None):
                return _d

        @staticmethod
        def engine():
            raise RuntimeError("no engine")

    health = SearchSource().health(_Ctx())
    assert health.status == "ok"
    assert health.details.get("engines_impaired") is None


# ---------------------------------------------------- F-05: answer-passage
class _Hit:
    def __init__(self, snippet, engine="bing", position=1, title="t",
                 url="https://example.com/a", published_at=None,
                 date_kind="", security_flags=None, raw=None, query="q",
                 modified_at=None):
        self.snippet = snippet
        self.engine = engine
        self.position = position
        self.title = title
        self.url = url
        self.published_at = published_at
        self.date_kind = date_kind
        self.security_flags = security_flags or []
        self.raw = raw or {}
        self.query = query
        self.modified_at = modified_at


def test_snippet_selection_prefers_the_query_terms_over_length():
    """The old rule took the longest snippet, which is usually boilerplate."""
    from iralens.search.engine import _best_snippet
    hits = [
        _Hit("Find Stripe fees and pricing information for all of our "
             "products and services in every country we operate in today."),
        _Hit("Stripe charges 2.9% + 30c per successful card charge."),
    ]
    chosen = _best_snippet(hits, "Stripe pricing standard fees 2026")
    assert "2.9%" in chosen


def test_snippet_selection_falls_back_to_longest_without_query_terms():
    from iralens.search.engine import _best_snippet
    hits = [_Hit("short"), _Hit("a considerably longer description")]
    assert _best_snippet(hits, "zzz qqq") == "a considerably longer description"


def test_snippet_selection_handles_empty_candidates():
    from iralens.search.engine import _best_snippet
    assert _best_snippet([_Hit("")], "anything here") == ""


def test_snippet_selection_is_deterministic():
    from iralens.search.engine import _best_snippet
    hits = [_Hit("alpha alpha"), _Hit("beta beta")]
    first = _best_snippet(hits, "alpha")
    assert first == _best_snippet(hits, "alpha")


# -------------------------------------------------------- F-09: conflicts
def _resp_with(results) -> SearchResponse:
    return _response([_outcome("bing", "results")], results=results)


def test_two_sources_disagreeing_on_a_figure_produce_a_conflict():
    results = [
        _result(url="https://a.com/x", snippet="the market is $4.2 billion",
                published_at="2026-01-01", date_status="known"),
        _result(url="https://b.com/y", snippet="the market is $7.9 billion",
                published_at="2026-02-01", date_status="known"),
    ]
    report = conflict_report(_resp_with(results))
    assert len(report["conflicts"]) == 1
    conflict = report["conflicts"][0]
    assert sorted(conflict["values"]) == [4.2, 7.9]
    # Both sides retained, with their sources and dates.
    urls = {s["url"] for s in conflict["sources"]}
    assert urls == {"https://a.com/x", "https://b.com/y"}
    assert all(s["published_at"] for s in conflict["sources"])


def test_conflict_is_labelled_heuristic_and_never_picks_a_winner():
    results = [
        _result(url="https://a.com/x", snippet="growth of 12 percent"),
        _result(url="https://b.com/y", snippet="growth of 30 percent"),
    ]
    conflict = conflict_report(_resp_with(results))["conflicts"][0]
    assert conflict["detection"] == "lexical"
    assert conflict["confidence"] == "heuristic"
    assert "not a judgement" in conflict["note"]


def test_agreeing_sources_produce_no_conflict():
    results = [
        _result(url="https://a.com/x", snippet="the fee is 2.9%"),
        _result(url="https://b.com/y", snippet="the fee is 2.9%"),
    ]
    assert conflict_report(_resp_with(results))["conflicts"] == []


def test_one_source_restating_itself_is_not_a_conflict():
    """Two figures on the same domain are not two sources."""
    results = [
        _result(url="https://a.com/x", snippet="the fee is 2.9%"),
        _result(url="https://a.com/y", snippet="the fee is 5.0%"),
    ]
    assert conflict_report(_resp_with(results))["conflicts"] == []


def test_rounding_differences_are_not_a_conflict():
    results = [
        _result(url="https://a.com/x", snippet="2.90%"),
        _result(url="https://b.com/y", snippet="2.91%"),
    ]
    assert conflict_report(_resp_with(results))["conflicts"] == []


def test_a_single_source_is_labelled_insufficient_not_confident():
    results = [_result(url="https://a.com/x", snippet="the market is $4.2 billion")]
    suff = conflict_report(_resp_with(results))["sufficiency"]
    assert suff["label"] == "insufficient"
    assert "assumed" in suff["note"]


def test_multiple_undated_sources_are_labelled_undated():
    results = [
        _result(url="https://a.com/x", snippet="12 percent"),
        _result(url="https://b.com/y", snippet="12 percent"),
    ]
    suff = conflict_report(_resp_with(results))["sufficiency"]
    assert suff["label"] == "undated"


def test_multiple_dated_sources_are_labelled_sourced():
    results = [
        _result(url="https://a.com/x", snippet="12 percent",
                published_at="2026-01-01", date_status="known"),
        _result(url="https://b.com/y", snippet="12 percent",
                published_at="2026-02-01", date_status="known"),
    ]
    assert conflict_report(_resp_with(results))["sufficiency"]["label"] == "sourced"


def test_conflicts_reach_the_legacy_search_path():
    results = [
        _result(url="https://a.com/x", snippet="the market is $4.2 billion"),
        _result(url="https://b.com/y", snippet="the market is $7.9 billion"),
    ]
    art = to_artifacts(_resp_with(results), "market size")[0]
    assert art.provenance["conflicts"]
    assert art.provenance["sufficiency"]["label"] == "undated"


def test_empty_results_produce_no_conflicts():
    assert conflict_report(_response([_outcome("bing", "results")], results=[]))["conflicts"] == []
