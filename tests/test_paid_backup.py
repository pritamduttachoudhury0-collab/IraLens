# -*- coding: utf-8 -*-
"""Offline tests for the optional, explicitly configured paid fallback."""

import json

from iralens.config import Config
from iralens.search.engines import paid_backup as paid_mod
from iralens.search.engines.paid_backup import PaidSearchBackupBackend, parse_paid_payload
from iralens.search.schema import SearchFilters


class Context:
    def __init__(self):
        self.config = Config(read_only=True)


def test_normalizes_brave_serper_and_tavily_payloads():
    brave = parse_paid_payload("brave", {"web": {"results": [
        {"title": "B", "url": "https://example.org/b", "description": "snippet"}
    ]}}, "q", 5)
    serper = parse_paid_payload("serper", {"organic": [
        {"title": "S", "link": "https://example.org/s", "snippet": "snippet", "date": "2025-01-02"}
    ]}, "q", 5)
    tavily = parse_paid_payload("tavily", {"results": [
        {"title": "T", "url": "https://example.org/t", "content": "snippet"}
    ]}, "q", 5)
    assert [hits[0].engine for hits in (brave, serper, tavily)] == ["paid-backup"] * 3
    assert serper[0].published_at == "2025-01-02"
    assert parse_paid_payload("serper", {"organic": [{"link": "https://x.test"}]}, "q", 5) == []


def test_serper_fallback_sends_key_in_header_not_url(monkeypatch):
    monkeypatch.setenv("IRALENS_BACKUP_SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("IRALENS_BACKUP_SEARCH_API_KEY", "secret-test-value")
    seen = {}

    def fake_fetch(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return json.dumps({"organic": [
            {"title": "Result", "link": "https://example.org", "snippet": "local test"}
        ]})

    monkeypatch.setattr(paid_mod, "fetch_url_text", fake_fetch)
    hits = PaidSearchBackupBackend().search("example query", SearchFilters(), 5, Context())
    assert hits[0].title == "Result"
    assert seen["url"] == "https://google.serper.dev/search"
    assert seen["headers"]["X-API-KEY"] == "secret-test-value"
    assert seen["same_origin_redirects"] is True
    assert "secret-test-value" not in seen["url"]
    assert json.loads(seen["data"]) == {"q": "example query", "num": 10}
