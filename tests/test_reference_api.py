# -*- coding: utf-8 -*-
"""The optional paid-reference comparison can be measured offline with fakes."""

from benchmarks import reference_api


def test_extracts_results_for_supported_reference_providers():
    samples = {
        "brave": {"web": {"results": [{"url": "https://example.org", "title": "A", "description": "x"}]}},
        "serper": {"organic": [{"link": "https://example.org", "title": "A", "snippet": "x"}]},
        "google-cse": {"items": [{"link": "https://example.org", "title": "A", "snippet": "x"}]},
        "tavily": {"results": [{"url": "https://example.org", "title": "A", "content": "x"}]},
    }
    for provider, payload in samples.items():
        results = reference_api._extract_results(provider, payload)
        assert results == [{"url": "https://example.org", "title": "A", "snippet": "x"}]


def test_searxng_reference_uses_explicit_https_endpoint_without_a_paid_key(monkeypatch):
    monkeypatch.delenv("SEARCH_API_REFERENCE_KEY", raising=False)
    monkeypatch.setenv("SEARCH_API_REFERENCE_PROVIDER", "searxng")
    monkeypatch.setenv("SEARCH_API_REFERENCE_URL", "https://search.example/search")
    monkeypatch.setattr(reference_api.time, "sleep", lambda _seconds: None)
    seen = []

    def request(provider, url, key, query, count, cx):
        seen.append((provider, url, key))
        return 200, {"results": [
            {"title": "Example", "url": "https://example.org/page", "content": "matched"}
        ]}

    monkeypatch.setattr(reference_api, "_request", request)
    queries = [{"id": "q1", "query": "q", "acceptable_domains": ["example.org"],
                "expected_phrases": []}]
    report = reference_api.run_reference_phase(queries, [], log=lambda _message: None)
    assert report["ran"] is True and report["provider"] == "searxng"
    assert report["reference_availability"] == 1.0
    assert seen == [("searxng", "https://search.example/search", "")]


def test_reference_phase_distinguishes_empty_results_from_api_failure(monkeypatch):
    monkeypatch.setenv("SEARCH_API_REFERENCE_KEY", "not-a-real-key")
    monkeypatch.setenv("SEARCH_API_REFERENCE_PROVIDER", "serper")
    monkeypatch.delenv("SEARCH_API_REFERENCE_URL", raising=False)
    monkeypatch.setattr(reference_api.time, "sleep", lambda _seconds: None)

    def request(_provider, _url, _key, query, _count, _cx):
        return (200, {"organic": []}) if query == "empty" else (429, {})

    monkeypatch.setattr(reference_api, "_request", request)
    queries = [
        {"id": "empty", "query": "empty", "acceptable_domains": [], "expected_phrases": []},
        {"id": "blocked", "query": "blocked", "acceptable_domains": [], "expected_phrases": []},
    ]
    report = reference_api.run_reference_phase(queries, [], log=lambda _message: None)
    assert report["reference_availability"] == 0.5
    assert report["reference_nonempty_result_rate"] == 0.0
    assert report["failure_rate"] == 0.5
    assert report["per_query"][0]["reference_available"] is True
    assert report["per_query"][0]["reference_returned_results"] is False
    assert report["per_query"][1]["reference_error"] == "http_429"
    assert report["reference_p50_latency_seconds"] is not None


def test_reference_phase_reports_gold_quality_availability_speed_and_overlap(monkeypatch):
    monkeypatch.setenv("SEARCH_API_REFERENCE_KEY", "not-a-real-key")
    monkeypatch.setenv("SEARCH_API_REFERENCE_PROVIDER", "serper")
    monkeypatch.delenv("SEARCH_API_REFERENCE_URL", raising=False)
    monkeypatch.setattr(reference_api.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        reference_api, "_request",
        lambda *args, **kwargs: (200, {"organic": [
            {"title": "Canberra", "link": "https://www.example.org/canberra", "snippet": "Canberra is the capital."}
        ]}),
    )
    queries = [
        {"id": "q1", "query": "capital", "acceptable_domains": ["example.org"], "expected_phrases": ["Canberra"]},
        {"id": "q2", "query": "capital", "acceptable_domains": [], "expected_phrases": ["Canberra"]},
    ]
    ira = [
        {"id": "q1", "available": True, "quality_hit": True, "top5": [{"host": "example.org"}]},
        {"id": "q2", "available": False, "quality_hit": False, "top5": []},
    ]
    report = reference_api.run_reference_phase(queries, ira, log=lambda _message: None)
    assert report["ran"] is True
    assert report["queries_requested"] == 2 and report["error_count"] == 0
    assert report["reference_availability"] == 1.0
    assert report["reference_gold_quality_at_5"] == 1.0
    assert report["iralens_gold_quality_at_5"] == 0.5
    assert report["gold_quality_delta_reference_minus_iralens"] == 0.5
    assert report["domain_overlap_at_5"] == 0.5
    assert report["reference_p50_latency_seconds"] is not None
    assert "not-a-real-key" not in str(report)
