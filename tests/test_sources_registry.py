# -*- coding: utf-8 -*-
"""Source registry, URL routing, and capability-catalog invariants."""

from halfiralens.sources import ALL_SOURCES, get_source, route_url, source_names
from halfiralens.sources.base import SourceHealth


def test_registry_shape():
    names = source_names()
    assert len(names) == len(set(names)), "source names must be unique"
    assert names[-1] == "web", "web must stay last (universal fallback)"
    assert "web-search" in names
    for source in ALL_SOURCES:
        assert source.name and source.description
        assert isinstance(source.operations, dict)
        for op, spec in source.operations.items():
            assert isinstance(spec, dict) and "description" in spec


EXPECTED_PLATFORMS = {
    "github", "youtube", "twitter", "reddit", "bilibili", "xiaohongshu",
    "v2ex", "xueqiu", "boss", "linkedin", "facebook", "instagram",
    "transcribe", "rss", "web-search", "web",
}


def test_all_platforms_present():
    assert EXPECTED_PLATFORMS <= set(source_names())


def test_url_routing():
    cases = {
        "https://github.com/o/r": "github",
        "https://www.youtube.com/watch?v=x": "youtube",
        "https://x.com/user/status/1": "twitter",
        "https://www.reddit.com/r/a/comments/b": "reddit",
        "https://www.bilibili.com/video/BV1xx": "bilibili",
        "https://www.v2ex.com/t/123": "v2ex",
        "https://xueqiu.com/S/NVDA": "xueqiu",
        "https://hnrss.org/frontpage?x=/feed": "rss",
        "https://example.com/article": None,  # generic web handled by fallback
    }
    for url, expected in cases.items():
        source = route_url(url)
        assert (source.name if source else None) == expected, url


def test_health_contract(isolated_home):
    """Every source answers health() with a well-formed SourceHealth."""
    from halfiralens.config import Config
    from halfiralens.core import Context
    from halfiralens.engine.native import BrowserEngine
    from halfiralens.session import Session

    context = Context(Config(), Session(), lambda: BrowserEngine())
    for source in ALL_SOURCES:
        health = source.health(context)
        assert isinstance(health, SourceHealth)
        assert health.status in ("ok", "warn", "off", "error")
        assert health.message
        # AI-facing health text must not leak internal component identity.
        lowered = (health.message + " " + health.hint).lower()
        assert "obscura" not in lowered
        assert "agent-reach" not in lowered and "agent_reach" not in lowered


def test_get_source():
    assert get_source("github").name == "github"
    assert get_source("nope") is None
