# -*- coding: utf-8 -*-
"""Offline tests for the official MediaWiki API backend."""

import json

from iralens.config import Config
from iralens.search.engines import wikipedia as wikipedia_mod
from iralens.search.engines.wikipedia import WikipediaBackend, parse_wikipedia_payload
from iralens.search.schema import SearchFilters

PAYLOAD = {
    "query": {
        "search": [
            {"title": "Solar energy", "snippet": "Use <span class=\"searchmatch\">solar</span> power.",
             "timestamp": "2025-04-03T10:00:00Z"},
            {"title": "", "snippet": "invalid"},
        ]
    }
}


class Context:
    def __init__(self):
        self.config = Config(read_only=True)


def test_wikipedia_parser_normalizes_search_api_payload():
    hits = parse_wikipedia_payload(PAYLOAD, "solar energy", 5)
    assert len(hits) == 1
    assert hits[0].engine == "wikipedia"
    assert hits[0].title == "Solar energy"
    assert hits[0].url == "https://en.wikipedia.org/wiki/Solar_energy"
    assert hits[0].snippet == "Use solar power."
    assert hits[0].published_at == "2025-04-03"
    assert parse_wikipedia_payload({"query": {"search": "wrong"}}, "q", 5) == []
    assert parse_wikipedia_payload(None, "q", 5) == []


def test_wikipedia_backend_uses_official_api_and_identifying_user_agent(monkeypatch):
    seen = {}

    def fake_fetch(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return json.dumps(PAYLOAD)

    monkeypatch.setattr(wikipedia_mod, "fetch_url_text", fake_fetch)
    monkeypatch.setattr(wikipedia_mod, "_pace_api_calls", lambda: None)
    hits = WikipediaBackend().search("solar energy", SearchFilters(), 5, Context())
    assert hits and "w/api.php" in seen["url"]
    assert "srsearch=solar+energy" in seen["url"]
    assert "User-Agent" in seen["headers"]
    assert hits[0].engine == "wikipedia"
