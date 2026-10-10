# -*- coding: utf-8 -*-
"""SearXNG backend (D-078): pure parsing plus rotation/cooldown behavior.

The network step is faked; a controllable clock makes cooldown expiry exact.
"""

import json

import pytest

from iralens.config import Config
from iralens.errors import SourceUnavailableError
from iralens.search.engines import searxng as searxng_mod
from iralens.search.engines.searxng import SearxngBackend, parse_response
from iralens.search.schema import SearchFilters

SAMPLE = {
    "query": "solar",
    "results": [
        {"title": "Solar report", "url": "https://a.example.edu/solar",
         "content": "Efficiency results.", "publishedDate": "2025-01-02"},
        {"title": "No url", "url": "", "content": "x"},
        {"title": "Bad scheme", "url": "ftp://x/y", "content": "x"},
        {"title": "Plain", "url": "https://b.example.org/p", "content": "snip"},
    ],
}


class Ctx:
    """Context stub carrying a real Config so IRALENS_* env overrides apply."""

    def __init__(self):
        self.config = Config(read_only=True)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_parse_response_normalizes_and_filters():
    hits = parse_response(SAMPLE)
    assert [h["url"] for h in hits] == ["https://a.example.edu/solar", "https://b.example.org/p"]
    assert hits[0]["published"] == "2025-01-02"
    assert parse_response({"results": []}) == []
    assert parse_response({"nope": 1}) == []
    assert parse_response(["not", "a", "dict"]) == []
    assert parse_response(None) == []


def test_search_returns_normalized_hits(monkeypatch):
    monkeypatch.setattr(searxng_mod, "fetch_url_text",
                        lambda url, **kw: json.dumps(SAMPLE))
    backend = SearxngBackend(clock=FakeClock())
    hits = backend.search("solar", SearchFilters(), 8, Ctx())
    assert [h.url for h in hits] == ["https://a.example.edu/solar", "https://b.example.org/p"]
    assert hits[0].published_at == "2025-01-02"
    assert hits[0].engine == "searxng"


def test_instances_come_from_environment(monkeypatch):
    monkeypatch.setenv("IRALENS_SEARXNG_INSTANCES", "https://one.test, https://two.test/")
    seen = []

    def fake_fetch(url, **kw):
        seen.append(url)
        return json.dumps(SAMPLE)

    monkeypatch.setattr(searxng_mod, "fetch_url_text", fake_fetch)
    backend = SearxngBackend(clock=FakeClock())
    backend.search("q", SearchFilters(), 8, Ctx())
    assert seen[0].startswith("https://one.test/search?")
    assert "format=json" in seen[0]


def test_rotation_prefers_the_next_instance(monkeypatch):
    monkeypatch.setenv("IRALENS_SEARXNG_INSTANCES", "https://one.test,https://two.test")
    seen = []
    monkeypatch.setattr(searxng_mod, "fetch_url_text",
                        lambda url, **kw: seen.append(url) or json.dumps(SAMPLE))
    backend = SearxngBackend(clock=FakeClock())
    backend.search("q", SearchFilters(), 8, Ctx())
    backend.search("q", SearchFilters(), 8, Ctx())
    assert seen[0].startswith("https://one.test")
    assert seen[1].startswith("https://two.test")


def test_failed_instance_goes_on_cooldown_and_next_is_tried(monkeypatch):
    monkeypatch.setenv("IRALENS_SEARXNG_INSTANCES", "https://one.test,https://two.test")
    clock = FakeClock()
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        if url.startswith("https://one.test"):
            raise SourceUnavailableError("boom", detail="http_503")
        return json.dumps(SAMPLE)

    monkeypatch.setattr(searxng_mod, "fetch_url_text", fake_fetch)
    backend = SearxngBackend(clock=clock)
    hits = backend.search("q", SearchFilters(), 8, Ctx())
    assert hits and calls[1].startswith("https://two.test")

    # While one.test cools down, only two.test is contacted.
    calls.clear()
    backend.search("q", SearchFilters(), 8, Ctx())
    assert [u.split("/")[2] for u in calls] == ["two.test"]

    # After the cooldown expires, one.test is tried again (rotation restores it).
    clock.now += 601
    calls.clear()
    backend.search("q", SearchFilters(), 8, Ctx())
    assert any("one.test" in u for u in calls)


def test_cooldown_duration_is_configurable(monkeypatch):
    monkeypatch.setenv("IRALENS_SEARXNG_INSTANCES", "https://one.test")
    monkeypatch.setenv("IRALENS_SEARXNG_COOLDOWN_SECONDS", "10")
    clock = FakeClock()
    calls = []
    monkeypatch.setattr(searxng_mod, "fetch_url_text",
                        lambda url, **kw: calls.append(url) or (_ for _ in ()).throw(
                            SourceUnavailableError("boom", detail="http_503")))
    backend = SearxngBackend(clock=clock)
    with pytest.raises(SourceUnavailableError):
        backend.search("q", SearchFilters(), 8, Ctx())
    with pytest.raises(SourceUnavailableError) as exc:
        backend.search("q", SearchFilters(), 8, Ctx())
    assert exc.value.detail == "all_instances_cooling_down"
    assert len(calls) == 1                      # the cooling instance was skipped
    clock.now += 11
    with pytest.raises(SourceUnavailableError):
        backend.search("q", SearchFilters(), 8, Ctx())
    assert len(calls) == 2                      # cooldown expired; retried


def test_all_instances_failing_reports_honestly(monkeypatch):
    monkeypatch.setenv("IRALENS_SEARXNG_INSTANCES", "https://one.test,https://two.test")
    monkeypatch.setattr(searxng_mod, "fetch_url_text",
                        lambda url, **kw: (_ for _ in ()).throw(
                            SourceUnavailableError("boom", detail="http_503")))
    backend = SearxngBackend(clock=FakeClock())
    with pytest.raises(SourceUnavailableError) as exc:
        backend.search("q", SearchFilters(), 8, Ctx())
    assert "all configured instances failed" in exc.value.message


def test_non_json_response_is_a_failure_not_a_result(monkeypatch):
    monkeypatch.setenv("IRALENS_SEARXNG_INSTANCES", "https://one.test,https://two.test")

    def fake_fetch(url, **kw):
        if url.startswith("https://one.test"):
            return "<html>captcha</html>"
        return json.dumps(SAMPLE)

    monkeypatch.setattr(searxng_mod, "fetch_url_text", fake_fetch)
    backend = SearxngBackend(clock=FakeClock())
    hits = backend.search("q", SearchFilters(), 8, Ctx())
    assert hits   # came from the second instance


def test_empty_result_set_is_honest_empty(monkeypatch):
    monkeypatch.setattr(searxng_mod, "fetch_url_text",
                        lambda url, **kw: json.dumps({"results": []}))
    backend = SearxngBackend(clock=FakeClock())
    assert backend.search("nothing matches", SearchFilters(), 8, Ctx()) == []


def test_query_operators_emulated_for_domains_and_filetype():
    backend = SearxngBackend(clock=FakeClock())
    filters = SearchFilters.build(include_domains=["example.org"], file_type="pdf")
    assert backend.build_query("solar", filters) == "site:example.org solar filetype:pdf"
    assert "language=en" in backend._build_url(
        "https://x.test", "q", SearchFilters.build(language="en"), 5)
