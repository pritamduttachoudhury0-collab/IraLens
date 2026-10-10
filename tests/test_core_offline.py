# -*- coding: utf-8 -*-
"""Facade behavior tests — no network, no engine (dependencies stubbed)."""

import pytest

from iralens import IraLens
from iralens.errors import OperationUnsupportedError, SecurityBlockedError
from iralens.model import Artifact
from iralens.sources import get_source


@pytest.fixture
def hil(isolated_home):
    instance = IraLens()
    yield instance
    instance.close()


def test_open_rejects_ssrf(hil):
    with pytest.raises(SecurityBlockedError):
        hil.open("http://169.254.169.254/latest/meta-data")
    with pytest.raises(SecurityBlockedError):
        hil.open("file:///etc/passwd")


def test_fetch_unknown_source(hil):
    with pytest.raises(OperationUnsupportedError) as exc_info:
        hil.fetch("nope", "whatever")
    assert "github" in exc_info.value.hint  # hint lists real sources


def test_fetch_unknown_op(hil):
    with pytest.raises(OperationUnsupportedError):
        hil.fetch("github", "launch_missiles")


def test_search_records_discovery(hil, monkeypatch):
    fake = [
        Artifact(title="A", url="https://a.test", source="web-search", kind="search_result",
                 content="snippet a", retrieval_method="search:browser-search",
                 discovered_from="search:q1"),
        Artifact(title="B", url="https://b.test", source="web-search", kind="search_result",
                 retrieval_method="search:browser-search", discovered_from="search:q1"),
    ]
    monkeypatch.setattr(get_source("web-search"), "fetch", lambda op, params, ctx: fake)

    results = hil.search("q1", limit=2)
    assert [r.url for r in results] == ["https://a.test", "https://b.test"]

    state = hil.session_state()
    assert state["last_query"] == "q1"
    found = {d["url"] for d in state["discovered"]}
    assert {"https://a.test", "https://b.test"} <= found
    assert all(d["found_via"] == "search:q1" for d in state["discovered"])


def test_open_uses_specialized_source_first(hil, monkeypatch):
    """A GitHub URL must be served by the specialized source, not the browser."""
    calls = []

    def fake_read_url(self, url, context):
        calls.append(url)
        return Artifact(title="repo", url=url, source="github", kind="repo",
                        content="readme", retrieval_method="api:github")

    monkeypatch.setattr(type(get_source("github")), "read_url", fake_read_url)

    artifact = hil.open("https://github.com/o/r")
    assert calls == ["https://github.com/o/r"]
    assert artifact.source == "github"
    assert artifact.retrieval_method == "api:github"
    # recorded in the shared session
    assert hil.session_state()["history"][0]["source"] == "github"


def test_open_falls_through_when_source_unavailable(hil, monkeypatch):
    from iralens.errors import SourceUnavailableError

    def broken_read_url(self, url, context):
        raise SourceUnavailableError("backend missing")

    def fake_web_read(self, url, context, mode="auto", max_chars=20000):
        return Artifact(title="page", url=url, source="web", kind="page",
                        content="body", retrieval_method="browser")

    monkeypatch.setattr(type(get_source("github")), "read_url", broken_read_url)
    monkeypatch.setattr(type(get_source("web")), "read_url", fake_web_read)

    artifact = hil.open("https://github.com/o/r")
    assert artifact.source == "web"
    assert artifact.retrieval_method == "browser"


def test_open_source_mode_requires_source(hil, monkeypatch):
    from iralens.errors import SourceUnavailableError

    def broken_read_url(self, url, context):
        raise SourceUnavailableError("backend missing")

    monkeypatch.setattr(type(get_source("github")), "read_url", broken_read_url)
    with pytest.raises(SourceUnavailableError):
        hil.open("https://github.com/o/r", mode="source")


def test_discovered_from_propagates(hil, monkeypatch):
    def fake_web_read(self, url, context, mode="auto", max_chars=20000):
        return Artifact(title="page", url=url, source="web", kind="page",
                        content="body", retrieval_method="browser")

    monkeypatch.setattr(type(get_source("web")), "read_url", fake_web_read)
    artifact = hil.open("https://x.test", discovered_from="search:earlier")
    assert artifact.discovered_from == "search:earlier"


def test_session_reset(hil):
    hil.session.record_open("https://a.test", title="a")
    assert hil.session_state()["history_total"] >= 1
    hil.reset_session()
    assert hil.session_state()["history_total"] == 0
    assert hil.session_state()["discovered_total"] == 0


def test_sources_catalog_is_pure(hil):
    import json

    catalog = json.dumps(hil.sources(), ensure_ascii=False).lower()
    assert "obscura" not in catalog
    assert "agent-reach" not in catalog and "agent_reach" not in catalog


def test_open_enforces_max_chars_end_to_end(hil, monkeypatch, tmp_path):
    from iralens.sources import web as web_mod
    from iralens.cache import ResponseCache
    import io as _io

    body = ("# Big page\n\n" + ("lorem ipsum dolor sit amet " * 400)).encode()

    class FakeResp(_io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(web_mod, "safe_urlopen",
                        lambda url, *, timeout=30, headers=None: FakeResp(body))
    monkeypatch.setattr(web_mod, "ResponseCache",
                        lambda enabled=True: ResponseCache(tmp_path / "pages"))
    art = hil.open("https://example.org/big", mode="static", max_chars=300)
    assert len(art.content) <= 360
    assert art.metadata.get("truncated") is True
    # Local-first (D-077): plain-text/markdown bodies pass through the direct
    # reader; only HTML bodies go through the extractor.
    assert art.retrieval_method == "direct"
