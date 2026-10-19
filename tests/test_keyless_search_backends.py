"""Offline contracts for official keyless specialist-search sources."""
from __future__ import annotations

from typing import List

from iralens.cache import ResponseCache
from iralens.reliability import ConcurrencyGate
from iralens.search import SearchEngine
from iralens.search.coverage import assess_coverage
from iralens.search.engines.arxiv import parse_arxiv_feed
from iralens.search.engines.base import SearchBackend
from iralens.search.engines.crossref import parse_crossref_payload
from iralens.search.engines.github import parse_github_payload
from iralens.search.engines.stackexchange import parse_stackexchange_payload
from iralens.search.intent import specialist_search_engines
from iralens.search.reformulate import reformulate
from iralens.search.schema import SearchHit, SearchOptions
from iralens.settings import Settings


def test_coverage_uses_token_boundaries_not_substrings():
    verdict = assess_coverage(
        "kit",
        [{"title": "Ticket inventory", "url": "https://example.org/tickets",
          "snippet": "A ticketing system for the office."}],
    )
    assert verdict["status"] == "low"


def test_jurisdiction_query_plan_keeps_the_official_form_in_the_default_budget():
    variants = reformulate("Assam passport renewal official procedure", max_queries=3)
    assert variants[0]["strategy"] == "original"
    assert any(variant["strategy"] == "jurisdiction" for variant in variants)


def test_specialist_routes_require_a_matching_search_intent():
    assert specialist_search_engines(
        '"Attention Is All You Need" original paper arXiv DOI') == ("crossref", "arxiv")
    assert specialist_search_engines(
        "site:github.com/scipy/scipy latest release") == ("github-search",)
    assert specialist_search_engines(
        "nginx reverse proxy websocket version specific known issue") == ("stackexchange",)
    assert specialist_search_engines("GitHub API rate limits official policy") == ()
    assert specialist_search_engines("capital of Assam") == ()


def test_crossref_parser_preserves_doi_abstract_and_publication_date():
    hits = parse_crossref_payload({
        "message": {"items": [{
            "DOI": "10.1234/example.1",
            "title": ["A paper about exoplanet atmospheres"],
            "URL": "https://doi.org/10.1234/example.1",
            "author": [{"given": "Ada", "family": "Example"}],
            "container-title": ["Journal of Example Research"],
            "published-print": {"date-parts": [[2025, 3, 14]]},
            "abstract": "<jats:p>We measure exoplanet atmospheres.</jats:p>",
            "score": 12.5,
        }]}
    }, "exoplanet atmosphere original paper", 5)
    assert len(hits) == 1
    assert hits[0].url == "https://doi.org/10.1234/example.1"
    assert hits[0].published_at == "2025-03-14"
    assert hits[0].date_kind == "publication"
    assert "We measure exoplanet atmospheres." in hits[0].snippet
    assert hits[0].raw["provider"] == "crossref"


def test_arxiv_parser_normalizes_legacy_http_atom_id_and_sanitizes_markup():
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2501.01234</id>
        <title>  A result about exoplanets </title>
        <summary>We study &amp; measure &lt;b&gt;atmospheres&lt;/b&gt;.</summary>
        <published>2025-01-09T12:00:00Z</published>
        <author><name>Ada Example</name></author>
      </entry>
    </feed>"""
    hits = parse_arxiv_feed(xml, "exoplanets original paper", 5)
    assert len(hits) == 1
    assert hits[0].url == "https://arxiv.org/abs/2501.01234"
    assert hits[0].snippet == "We study & measure atmospheres."
    assert hits[0].published_at == "2025-01-09"


def test_stackexchange_parser_rejects_non_stackoverflow_links():
    hits = parse_stackexchange_payload({"items": [
        {"title": "Configure NGINX WebSockets", "link": "https://stackoverflow.com/questions/1/x",
         "body": "<p>Set the <code>Upgrade</code> header.</p>", "is_answered": True,
         "score": 42, "tags": ["nginx", "websocket"]},
        {"title": "bad link", "link": "https://evil.example/x", "body": "x"},
    ]}, "nginx websocket", 10)
    assert len(hits) == 1
    assert hits[0].snippet == "Set the Upgrade header."
    assert hits[0].raw["answered"] is True


def test_github_parser_keeps_repo_license_and_separates_modified_date():
    hits = parse_github_payload({"items": [{
        "full_name": "scipy/scipy", "html_url": "https://github.com/scipy/scipy",
        "description": "Scientific tools", "license": {"spdx_id": "BSD-3-Clause"},
        "updated_at": "2026-10-18T12:00:00Z",
    }]}, "site:github.com/scipy/scipy license", 5, mode="repositories")
    assert len(hits) == 1
    assert "BSD-3-Clause" in hits[0].snippet
    assert hits[0].published_at is None
    assert hits[0].modified_at == "2026-10-18"
    assert hits[0].date_kind == "revision"


def _hit(name: str, title: str, url: str, snippet: str, query: str = "") -> SearchHit:
    return SearchHit(title=title, url=url, snippet=snippet, engine=name,
                     position=1, query=query)


class _Backend(SearchBackend):
    def __init__(self, name: str, factory):
        self.name = name
        self.factory = factory
        self.calls: List[str] = []

    def search(self, query, filters, limit, context):
        self.calls.append(query)
        return self.factory(query)


def _engine(tmp_path, backends, **settings):
    configured_defaults = tuple(
        name for name in ("duckduckgo", "bing")
        if any(backend.name == name for backend in backends)
    )
    values = {
        "search_engines": configured_defaults,
        "search_min_engines": 2,
        "retry_base_delay_seconds": 0.0,
        "retry_max_delay_seconds": 0.0,
        **settings,
    }
    return SearchEngine(
        Settings(**values), backends={backend.name: backend for backend in backends},
        cache=ResponseCache(tmp_path / "cache"), gate=ConcurrencyGate(4, 1.0),
    )


def test_adaptive_queries_stop_when_first_serp_is_adequate(tmp_path):
    query = "Python asyncio TaskGroup official documentation"
    backend = _Backend("duckduckgo", lambda q: [
        _hit("duckduckgo", "asyncio TaskGroup documentation",
             "https://docs.python.org/3/library/asyncio-task.html",
             "TaskGroup documentation for asyncio.", q),
    ])
    engine = _engine(tmp_path, [backend], search_min_engines=1)
    response = engine.search(query, options=SearchOptions(max_results=1, cache="bypass"))
    assert len(backend.calls) == 1
    assert len(response.queries) == 1
    assert response.coverage["status"] == "good"


def test_adaptive_queries_try_focus_after_low_coverage(tmp_path):
    backend = _Backend("duckduckgo", lambda q: [
        _hit("duckduckgo", "Unrelated general science page" if "replication" in q else
             "Exoplanet atmosphere spectroscopy",
             "https://example.org/paper",
             "General astronomy overview." if "replication" in q else
             "Spectroscopy measures exoplanet atmospheres.", q),
    ])
    engine = _engine(tmp_path, [backend], search_min_engines=1)
    question = "exoplanet atmosphere spectroscopy replication failures primary literature"
    response = engine.search(question, options=SearchOptions(max_results=1, cache="bypass"))
    assert len(backend.calls) == 2
    assert [q["strategy"] for q in response.queries] == ["original", "focus"]
    assert response.coverage["status"] == "good"


def test_intent_routed_specialists_precede_general_engines_but_respect_explicit_choice(tmp_path):
    calls: List[str] = []

    def result(name, title, url, snippet):
        def run(query):
            calls.append(name)
            return [_hit(name, title, url, snippet, query)]
        return run

    crossref = _Backend("crossref", result(
        "crossref", "Attention Is All You Need", "https://doi.org/10.5555/attention",
        "The transformer architecture paper."))
    arxiv = _Backend("arxiv", result(
        "arxiv", "Attention Is All You Need", "https://arxiv.org/abs/1706.03762",
        "We propose the Transformer."))
    ddg = _Backend("duckduckgo", result(
        "duckduckgo", "Attention Is All You Need", "https://example.org/paper",
        "A paper about transformers."))
    bing = _Backend("bing", result(
        "bing", "Attention Is All You Need", "https://example.net/paper",
        "A paper about transformers."))
    engine = _engine(tmp_path, [crossref, arxiv, ddg, bing])
    question = '"Attention Is All You Need" original paper arXiv DOI'
    engine.search(question, options=SearchOptions(max_results=1, reformulate=False, cache="bypass"))
    assert calls == ["crossref", "arxiv"]

    calls.clear()
    engine.search(question, options=SearchOptions(
        max_results=1, reformulate=False, cache="bypass", engines=("duckduckgo",)))
    assert calls == ["duckduckgo"]


def test_paid_quality_fallback_is_opt_in_and_attempted_once(tmp_path):
    query = "exoplanet atmosphere spectroscopy"
    weak = lambda name: _Backend(name, lambda q: [
        _hit(name, "Unrelated gardening article", f"https://{name}.example.org/a",
             "A page about flowers.", q),
    ])
    paid = _Backend("paid-backup", lambda q: [
        _hit("paid-backup", "Exoplanet atmosphere spectroscopy study",
             "https://answer.example.org/paper", "Spectroscopy measures exoplanet atmospheres.", q),
    ])
    engine = _engine(
        tmp_path, [weak("duckduckgo"), weak("bing"), paid],
        backup_search_provider="serper", backup_search_api_key="test-only",
        search_paid_fallback_on_low_coverage=True,
    )
    response = engine.search(query, options=SearchOptions(
        max_results=1, reformulate=False, cache="bypass"))
    assert len(paid.calls) == 1
    assert response.paid_fallback["attempted"] is True
    assert response.paid_fallback["trigger"] == "low_coverage"
    assert any(result.url == "https://answer.example.org/paper" for result in response.results)


def test_low_coverage_does_not_spend_paid_key_without_opt_in(tmp_path):
    weak = lambda name: _Backend(name, lambda q: [
        _hit(name, "Unrelated gardening article", f"https://{name}.example.org/a",
             "A page about flowers.", q),
    ])
    paid = _Backend("paid-backup", lambda q: [
        _hit("paid-backup", "Exoplanet atmosphere spectroscopy study",
             "https://answer.example.org/paper", "Spectroscopy measures exoplanet atmospheres.", q),
    ])
    engine = _engine(
        tmp_path, [weak("duckduckgo"), weak("bing"), paid],
        backup_search_provider="serper", backup_search_api_key="test-only",
    )
    response = engine.search("exoplanet atmosphere spectroscopy", options=SearchOptions(
        max_results=1, reformulate=False, cache="bypass"))
    assert paid.calls == []
    assert response.paid_fallback["configured"] is True
    assert response.paid_fallback["attempted"] is False
    assert response.paid_fallback["trigger"] == "low_coverage_not_enabled"


def test_benchmark_replacement_metric_requires_gold_hit_without_paid_call():
    from benchmarks.run_benchmark import SearchObservation, score_query

    entry = {
        "id": "q1", "category": "technical", "split": "dev",
        "query": "exoplanet atmosphere spectroscopy",
        "acceptable_domains": ["nasa.gov"], "expected_phrases": [],
    }
    free = SearchObservation(query=entry["query"], results=[{
        "url": "https://science.nasa.gov/exoplanets/", "title": "Exoplanets",
        "snippet": "Exoplanet atmosphere spectroscopy results.",
    }], outcomes=[{"engine": "duckduckgo", "status": "results"}])
    assert score_query(entry, free)["free_quality_hit"] is True

    paid = SearchObservation(query=entry["query"], results=list(free.results),
                             outcomes=[*free.outcomes,
                                       {"engine": "paid-backup", "status": "results"}])
    paid_score = score_query(entry, paid)
    assert paid_score["quality_hit"] is True
    assert paid_score["paid_attempted"] is True
    assert paid_score["free_quality_hit"] is False
