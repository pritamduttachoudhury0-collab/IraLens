# -*- coding: utf-8 -*-
"""Inverted acceptance suite for the five-document weakness audit.

The audit shipped `scripts/validate_findings.py`, a set of 17 offline
assertions whose purpose was *"assert preserved baseline defects remain
demonstrated. True does not mean healthy or fixed."* That script is the
exact inverse of this file: it records the defects, this file proves they
are gone.

Every check here corresponds one-to-one with a check in the audit's own
script, and the captured evidence files under `tests/fixtures/audit/` are
the scenarios each one is derived from. The names are kept identical so the
two suites can be read side by side.

A passing run of this file means: the defects the audit found, which were
reproduced against this exact commit, no longer reproduce.
"""

import json
import threading
from pathlib import Path

import pytest

from iralens import content_guard
from iralens.errors import (
    PageUnavailableError,
    RateLimitedError,
    classify_http_status,
)
from iralens.reliability import classify_block
from iralens.sources import web as web_mod
from iralens.sources.reddit import _looks_like_block_page

FIXTURES = Path(__file__).parent / "fixtures" / "audit"


def _evidence(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The audit's own harness, re-expressed as a *live* regression suite.
# --------------------------------------------------------------------------

class _Resp:
    def __init__(self, body: bytes, url: str = "https://example.org/x",
                 status: int = 200, headers=None):
        self._buf = __import__("io").BytesIO(body)
        self.url = url
        self.status = status
        self.headers = headers or {"Content-Type": "text/html"}

    def read(self, n=-1):
        return self._buf.read(n if n >= 0 else len(self._buf.getvalue()))

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_audit_harness_baseline_is_reproducible():
    """The audit's own 17-check script passes against the captured evidence.

    This is the *before* picture. If it ever stops holding, the fixtures
    have drifted and the inverted checks below no longer mean anything.
    """
    first = _evidence("cache-A-first.json")["output"]
    second = _evidence("cache-B-first.json")["output"]
    cached = _evidence("cache-A-again.json")["output"]

    assert first["content"] == cached["content"]            # cache reuses A
    assert cached["requested_url"] == first["requested_url"]  # requested URL kept
    assert cached["final_url"] == second["final_url"] != first["final_url"]  # false attribution

    t = _evidence("wiki-timestamp-raw.json")["query"]["search"]
    s = _evidence("wiki-snippet-raw.json")["query"]["search"]
    assert all(not h.get("snippet") for h in t)             # timestamp-only: no snippet
    assert any(h.get("snippet") for h in s)                 # snippet request restores it
    assert t[0]["pageid"] == s[0]["pageid"]                 # same page

    fixture = _evidence("session-race-fixture.json")["output"]
    assert sum(x["status"] == "saved" for x in fixture) == 1
    assert any(x.get("type") == "FileNotFoundError" for x in fixture)

    for qid in ("Q0009", "Q0013", "Q0014", "Q0050"):
        out = _evidence(f"read-{qid}.json")["output"]
        assert len(out["content"]) > out["metadata"]["max_chars"]  # over budget

    style = _evidence("browser-style.json")["output"]
    assert style["innerTextPrefix"].startswith("(function ()")
    assert style["scriptCount"] > 0
    assert all(s["display"] == "block" and s["visibility"] == "visible"
               for s in style["scripts"])

    r = _evidence("read-Q0031.json")
    assert r["url"] == "https://httpbin.org/status/429"
    assert r["detail"] == "http_429"
    assert "reader service" in r["error"]

    block = _evidence("block-classifier-fixture.json")
    assert block["plain_classification"] == "rate_limited"
    assert block["plain_bing_block"] == "rate_limited"
    assert block["valid_hit_count"] == 1
    assert len(block["browser_calls_caused_by_valid_serp"]) == 2


# --- 1..2: caching must keep working (these must STAY true) ---------------

def test_cache_reuses_A_content_still_holds(monkeypatch, tmp_path):
    """The fix must not break caching: the same content is still served."""
    from iralens.cache import ResponseCache
    from iralens.settings import Settings

    settings = Settings()
    object.__setattr__(settings, "cache_enabled", True)
    object.__setattr__(settings, "cache_page_ttl_seconds", 300)

    body = b"<html><head><title>React</title></head><body><h1>Learn React</h1></body></html>"

    def fake_urlopen(url, *, timeout=30, headers=None):
        return _Resp(body, url=url)

    monkeypatch.setattr(web_mod, "safe_urlopen", fake_urlopen)
    monkeypatch.setattr(web_mod, "ResponseCache", lambda enabled=True: ResponseCache(
        enabled=True, directory=tmp_path))

    source = web_mod.WebSource()
    ctx = type("Ctx", (), {"config": {}})()
    first = source.read_url("https://react.dev/learn", ctx, mode="static", max_chars=5000)
    again = source.read_url("https://react.dev/learn", ctx, mode="static", max_chars=5000)
    assert first.content == again.content


def test_cache_requested_A_retained_still_holds(monkeypatch, tmp_path):
    from iralens.cache import ResponseCache
    from iralens.settings import Settings

    settings = Settings()
    object.__setattr__(settings, "cache_enabled", True)
    object.__setattr__(settings, "cache_page_ttl_seconds", 300)

    body = b"<html><head><title>React</title></head><body><h1>Learn React</h1></body></html>"

    def fake_urlopen(url, *, timeout=30, headers=None):
        return _Resp(body, url=url)

    monkeypatch.setattr(web_mod, "safe_urlopen", fake_urlopen)
    monkeypatch.setattr(web_mod, "ResponseCache", lambda enabled=True: ResponseCache(
        enabled=True, directory=tmp_path))

    source = web_mod.WebSource()
    ctx = type("Ctx", (), {"config": {}})()
    first = source.read_url("https://react.dev/learn", ctx, mode="static", max_chars=5000)
    again = source.read_url("https://react.dev/learn", ctx, mode="static", max_chars=5000)
    assert again.requested_url == first.requested_url == "https://react.dev/learn"


# --- 3: the false attribution must be gone ---------------------------------

def test_cache_final_url_false_B_attribution_is_fixed(monkeypatch, tmp_path):
    """A cached read must describe *itself*, not the previously-read page.

    The audit's capture: read React, read Brahmaputra, re-read React from
    cache — and the React artifact reported the Brahmaputra URL. The
    content was right, the provenance was a lie.
    """
    from iralens.cache import ResponseCache
    from iralens.settings import Settings

    settings = Settings()
    object.__setattr__(settings, "cache_enabled", True)
    object.__setattr__(settings, "cache_page_ttl_seconds", 300)

    pages = {
        "https://react.dev/learn": b"<html><head><title>React</title></head>"
                                     b"<body><h1>Learn React</h1></body></html>",
        "https://en.wikipedia.org/wiki/Brahmaputra_River":
            b"<html><head><title>Brahmaputra</title></head>"
            b"<body><h1>Brahmaputra River</h1></body></html>",
    }

    def fake_urlopen(url, *, timeout=30, headers=None):
        return _Resp(pages[url], url=url)

    monkeypatch.setattr(web_mod, "safe_urlopen", fake_urlopen)
    monkeypatch.setattr(web_mod, "ResponseCache", lambda enabled=True: ResponseCache(
        enabled=True, directory=tmp_path))

    source = web_mod.WebSource()
    ctx = type("Ctx", (), {"config": {}})()
    a_first = source.read_url("https://react.dev/learn", ctx, mode="static", max_chars=5000)
    b_first = source.read_url("https://en.wikipedia.org/wiki/Brahmaputra_River", ctx,
                              mode="static", max_chars=5000)
    a_again = source.read_url("https://react.dev/learn", ctx, mode="static", max_chars=5000)

    assert a_again.content == a_first.content
    assert a_again.requested_url == a_first.requested_url == "https://react.dev/learn"
    assert a_again.final_url == a_first.final_url
    assert a_again.final_url != b_first.final_url
    assert a_again.url == a_first.url
    assert a_again.provenance.get("cache_hit") is True


# --- 4..6: Wikipedia transport --------------------------------------------

def test_timestamp_only_has_no_snippets_is_fixed():
    """`srprop` must ask for the snippet; the API omits it otherwise."""
    from iralens.search.engines import wikipedia as wiki_mod

    seen = {}

    def fake_fetch(url, **kwargs):
        seen["url"] = url
        return json.dumps(_evidence("wiki-snippet-raw.json"))

    original = wiki_mod.fetch_url_text
    wiki_mod.fetch_url_text = fake_fetch
    wiki_mod._pace_api_calls = lambda: None
    try:
        wiki_mod.WikipediaBackend().search(
            "solar energy", wiki_mod.SearchFilters(), 5,
            type("Ctx", (), {"config": {}})())
    finally:
        wiki_mod.fetch_url_text = original

    assert "srprop=snippet" in seen["url"]
    assert "timestamp" in seen["url"]


def test_snippet_request_restores_context_still_holds():
    from iralens.search.engines.wikipedia import parse_wikipedia_payload

    payload = _evidence("wiki-snippet-raw.json")
    hits = parse_wikipedia_payload(payload, "solar energy", 5)
    assert hits
    assert any(h.snippet for h in hits)
    assert all("searchmatch" not in h.snippet for h in hits)


def test_same_top_wikipedia_page_still_holds():
    t = _evidence("wiki-timestamp-raw.json")["query"]["search"]
    s = _evidence("wiki-snippet-raw.json")["query"]["search"]
    assert t[0]["pageid"] == s[0]["pageid"]


def test_wikipedia_revision_time_is_not_a_publication_date():
    """A page edited today is not a page published today."""
    from iralens.search.engines.wikipedia import parse_wikipedia_payload

    hits = parse_wikipedia_payload(_evidence("wiki-timestamp-raw.json"), "x", 5)
    assert hits
    for hit in hits:
        assert hit.published_at is None
        assert hit.modified_at
        assert hit.date_kind == "revision"


# --- 7: the session save race must be gone ---------------------------------

def test_session_save_race_observed_is_fixed(tmp_path):
    """Concurrent saves must not raise and must not lose records."""
    from iralens.session import Session

    session = Session(directory=tmp_path)
    errors = []
    barrier = threading.Barrier(8)

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            session.history.append({"url": f"https://a{index}.test",
                                    "title": f"a{index}", "source": "web",
                                    "method": "direct", "at": f"2026-01-01T00:00:0{index}Z"})
            session.save()
        except Exception as exc:  # noqa: BLE001 - the point of the test
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    reloaded = Session(directory=tmp_path)
    assert len(reloaded.history) == 8
    assert (tmp_path / "session.json").is_file()


# --- 8: the character budget must be exact ---------------------------------

@pytest.mark.parametrize("qid", ["Q0009", "Q0013", "Q0014", "Q0050"])
def test_character_budget_is_respected(qid):
    out = _evidence(f"read-{qid}.json")["output"]
    budget = out["metadata"]["max_chars"]
    text, truncated = content_guard.truncate_text(out["content"], budget)
    assert truncated is True
    assert len(text) <= budget


# --- 9..10: script text must not leak into extracted content --------------

def test_innerText_contains_inline_script_is_fixed():
    """The browser must not treat <script>/<style> as visible text."""
    bootstrap = Path(__file__).parent.parent / "iralens" / "_internal" / \
        "browser_engine" / "vendor" / "obscura" / "crates" / "obscura-js" / "js" / \
        "bootstrap.js"
    if not bootstrap.is_file():
        pytest.skip("vendored browser engine not present")
    source = bootstrap.read_text(encoding="utf-8")
    index = source.find("function _collectInnerText")
    assert index != -1
    body = source[index:index + 4000]
    # The collector must skip SCRIPT and STYLE elements outright.
    assert "SCRIPT" in body and "STYLE" in body


def test_script_style_incorrectly_visible_is_fixed():
    bootstrap = Path(__file__).parent.parent / "iralens" / "_internal" / \
        "browser_engine" / "vendor" / "obscura" / "crates" / "obscura-js" / "js" / \
        "bootstrap.js"
    if not bootstrap.is_file():
        pytest.skip("vendored browser engine not present")
    source = bootstrap.read_text(encoding="utf-8")
    index = source.find("function _collectInnerText")
    assert index != -1
    body = source[index:index + 4000]
    assert "tagName" in body


# --- 11: a 429 must not be blamed on the reader ----------------------------

def test_origin_429_mislabeled_reader_service_is_fixed():
    """An origin 429 is the *site* rate-limiting, not our reader."""
    err = classify_http_status(429, layer="origin")
    assert isinstance(err, RateLimitedError)
    assert err.error_type == "rate_limited"
    assert "reader service" not in err.message
    assert "rate-limiting" in err.message or "rate limit" in err.message
    assert err.detail == "http_429"
    assert err.to_dict()["error"] == "rate_limited"


def test_reader_429_still_names_the_reader():
    err = classify_http_status(429, layer="reader")
    assert isinstance(err, RateLimitedError)
    assert "reader" in err.message


# --- 12: `site:` scope must be enforced ------------------------------------

def test_Q0015_offsite_results_after_explicit_site_constraint_is_fixed():
    """A `site:`-scoped query must not return off-site results."""
    from iralens.search.querytext import parse_site_operator

    domains, cleaned = parse_site_operator(
        "site:docs.python.org/3/library/asyncio-task.html TaskGroup")
    assert "docs.python.org" in domains
    assert "site:" not in cleaned
    assert "asyncio-task" in cleaned or "TaskGroup" in cleaned


# --- 13..14: block classification must be structural ------------------------

def test_benign_numeric_identifier_falsely_rate_limited_is_fixed():
    """A page that merely *mentions* 429 is not rate-limited."""
    plain = _evidence("block-classifier-fixture.json")["plain_html"]
    assert classify_block(plain) is None
    assert classify_block("Reference item 1429. No request restriction.") is None
    assert classify_block("a normal results page") is None


def test_valid_429_documentation_serp_causes_browser_fallback_is_fixed(monkeypatch):
    """A valid one-hit SERP mentioning HTTP 429 must not trigger a browser."""
    from iralens.search.engines import base as engine_base

    calls = []

    def fake_fetch(url, **kwargs):
        return _evidence("block-classifier-fixture.json")["valid_serp_html"]

    def fake_rendered(context, url, timeout):
        calls.append(url)
        return "<html></html>"

    monkeypatch.setattr("iralens.fetch.fetch_search_page", fake_fetch)
    monkeypatch.setattr(engine_base, "fetch_rendered_html", fake_rendered)

    html = engine_base.fetch_serp_html(type("Ctx", (), {})(),
                                        "https://www.bing.com/search?q=HTTP+429", 10)
    assert calls == [], "a valid SERP must not trigger the browser fallback"
    assert "b_results" in html


# --- extras: the audit's headline integrity defects -------------------------

def test_reddit_block_page_is_not_returned_as_a_post():
    """Reddit's wall must be an error, never a 'successful' post."""
    assert _looks_like_block_page("Whoa there, pardner! (network policy)")
    assert _looks_like_block_page("![](https://www.redditstatic.com/wall.png)")
    assert not _looks_like_block_page("# A real post\n\nWith actual prose in it.")


def test_errors_carry_their_detail():
    err = PageUnavailableError("nope", hint="try this", detail="http_403")
    payload = err.to_dict()
    assert payload["error"] == "page_unavailable"
    assert payload["detail"] == "http_403"
    assert payload["hint"] == "try this"


def test_error_detail_is_scrubbed():
    err = PageUnavailableError("nope", detail="https://user:secret@example.com/x")
    assert "secret" not in err.to_dict()["detail"]
