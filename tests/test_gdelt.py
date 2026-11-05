"""Offline contracts for the public GDELT news-search adapter."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from iralens.config import Config
from iralens.search.engines import build_backends
from iralens.search.engines import gdelt as gdelt_mod
from iralens.search.engines.gdelt import GdeltBackend, parse_gdelt_payload
from iralens.search.schema import SearchFilters


class Context:
    def __init__(self) -> None:
        self.config = Config(read_only=True)


def test_gdelt_parser_uses_direct_article_urls_and_keeps_seen_time_nonpublication():
    payload = {"articles": [
        {"title": "  Central bank announces a decision  ",
         "url": "https://www.reuters.com/world/example/", "domain": "reuters.com",
         "seendate": "20261019T012345Z", "language": "English", "sourceCountry": "United States"},
        {"title": "Missing URL"},
        {"title": "Unsafe scheme", "url": "javascript:alert(1)"},
        {"title": "Malformed URL", "url": "https://["},
        {"title": "Second valid", "url": "https://example.org/story", "domain": "example.org"},
    ]}
    hits = parse_gdelt_payload(payload, "central bank news", 5)
    assert [hit.url for hit in hits] == [
        "https://www.reuters.com/world/example/", "https://example.org/story"]
    assert hits[0].engine == "gdelt"
    assert hits[0].title == "Central bank announces a decision"
    assert hits[0].published_at is None
    assert hits[0].raw["seen_at"] == "2026-10-19"
    assert hits[0].raw["domain"] == "reuters.com"
    assert parse_gdelt_payload({"articles": "not-a-list"}, "q", 5) == []


def test_gdelt_backend_calls_public_api_with_bounded_timeout_and_dates(monkeypatch):
    seen = {}

    def fake_fetch(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return json.dumps({"articles": [{
            "title": "Recent agency update", "url": "https://example.gov/update",
            "domain": "example.gov", "seendate": "20261018T120000Z",
        }]})

    monkeypatch.setattr(gdelt_mod, "fetch_url_text", fake_fetch)
    monkeypatch.setattr(gdelt_mod.HOST_RATE_LIMITER, "set_interval", lambda *args: None)
    monkeypatch.setattr(gdelt_mod.HOST_RATE_LIMITER, "wait", lambda *args: None)
    hits = GdeltBackend().search(
        "agency policy update news",
        SearchFilters.build(date_from="2026-10-01", date_to="2026-10-19"), 5, Context())

    query = parse_qs(urlsplit(seen["url"]).query)
    assert urlsplit(seen["url"]).netloc == "api.gdeltproject.org"
    assert query["mode"] == ["ArtList"]
    assert query["format"] == ["json"]
    assert query["query"] == ["agency policy update news"]
    assert query["maxrecords"] == ["30"]
    assert query["startdatetime"] == ["20261001000000"]
    assert query["enddatetime"] == ["20261019235959"]
    assert seen["timeout"] <= 8
    assert seen["headers"]["User-Agent"]
    assert len(hits) == 1
    assert hits[0].published_at is None


def test_gdelt_is_registered_and_refuses_unverified_pagination():
    assert "gdelt" in build_backends()
    assert GdeltBackend.paginated is False


def _patch_gdelt_io(monkeypatch, response_text, seen):
    def fake_fetch(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return response_text

    intervals = {}
    monkeypatch.setattr(gdelt_mod, "fetch_url_text", fake_fetch)
    monkeypatch.setattr(gdelt_mod.HOST_RATE_LIMITER, "set_interval",
                        lambda host, seconds: intervals.__setitem__(host, seconds))
    monkeypatch.setattr(gdelt_mod.HOST_RATE_LIMITER, "wait", lambda *args: None)
    return intervals


def test_gdelt_paces_at_the_published_five_second_limit(monkeypatch):
    seen: dict = {}
    intervals = _patch_gdelt_io(monkeypatch, json.dumps({"articles": []}), seen)
    GdeltBackend().search("central bank news", SearchFilters.build(), 5, Context())
    # The provider's published contract is one request every 5 seconds.
    assert intervals.get("api.gdeltproject.org") == 5.0


def test_gdelt_rate_limit_notice_is_rate_limited_not_invalid_json(monkeypatch):
    from iralens.errors import RateLimitedError
    seen: dict = {}
    # GDELT returns this as HTTP 200 plain text, not a 429 and not JSON.
    notice = "Please limit requests to one every 5 seconds."
    _patch_gdelt_io(monkeypatch, notice, seen)
    try:
        GdeltBackend().search("central bank news", SearchFilters.build(), 5, Context())
    except RateLimitedError as exc:
        assert exc.detail == "rate_limit_notice"
    else:
        raise AssertionError("rate-limit notice was not classified as RateLimitedError")


def test_gdelt_news_query_without_dates_gets_a_recency_timespan(monkeypatch):
    seen: dict = {}
    _patch_gdelt_io(monkeypatch, json.dumps({"articles": []}), seen)
    GdeltBackend().search("latest news on the climate summit", SearchFilters.build(), 5, Context())
    query = parse_qs(urlsplit(seen["url"]).query)
    assert "timespan" in query
    assert query["timespan"][0].endswith("d")
    # The raw natural-language query is still passed through unchanged.
    assert query["query"] == ["latest news on the climate summit"]


def test_gdelt_explicit_dates_suppress_the_timespan(monkeypatch):
    seen: dict = {}
    _patch_gdelt_io(monkeypatch, json.dumps({"articles": []}), seen)
    GdeltBackend().search(
        "latest news on the climate summit",
        SearchFilters.build(date_from="2026-10-01", date_to="2026-10-19"), 5, Context())
    query = parse_qs(urlsplit(seen["url"]).query)
    assert "timespan" not in query
    assert query["startdatetime"] == ["20261001000000"]
    assert query["enddatetime"] == ["20261019235959"]


def test_gdelt_non_news_query_gets_no_timespan(monkeypatch):
    seen: dict = {}
    _patch_gdelt_io(monkeypatch, json.dumps({"articles": []}), seen)
    GdeltBackend().search("history of the roman empire", SearchFilters.build(), 5, Context())
    query = parse_qs(urlsplit(seen["url"]).query)
    assert "timespan" not in query
