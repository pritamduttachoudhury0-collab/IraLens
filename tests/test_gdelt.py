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
