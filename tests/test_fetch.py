# -*- coding: utf-8 -*-
"""Direct HTTP transport (D-076): classification, caps, deadlines, policy.

Everything is offline: safe_urlopen is replaced by fakes. The transport must
be no weaker than the reader path it shares a threat model with.
"""

import io
import urllib.error

import pytest

from iralens import fetch as fetch_mod
from iralens.errors import (
    ExtractionError,
    OperationTimeoutError,
    PageUnavailableError,
    SecurityBlockedError,
)
from iralens.fetch import fetch_search_page, fetch_url_text


class FakeResponse:
    def __init__(self, body: bytes, chunk: int = 64 * 1024):
        self._buf = io.BytesIO(body)
        self._chunk = chunk

    def read(self, n=-1):
        size = self._chunk if n < 0 else min(n, self._chunk)
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class EndlessResponse:
    def __init__(self, chunk: int = 1, fill: bytes = b"a"):
        self._chunk = chunk
        self._fill = fill

    def read(self, n=-1):
        return self._fill * self._chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, response=None, error=None, seen=None):
    def fake_urlopen(url, *, timeout=30, headers=None):
        if seen is not None:
            seen.append((url, headers))
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(fetch_mod, "safe_urlopen", fake_urlopen)


def test_fetch_returns_decoded_text(monkeypatch):
    _patch(monkeypatch, response=FakeResponse("<html><p>hello</p></html>".encode()))
    assert fetch_search_page("https://example.org/serp") == "<html><p>hello</p></html>"


def test_fetch_sends_browser_like_user_agent(monkeypatch):
    seen = []
    _patch(monkeypatch, response=FakeResponse(b"page"), seen=seen)
    fetch_search_page("https://example.org/serp")
    headers = seen[0][1]
    assert "Mozilla" in headers["User-Agent"]
    assert "text/html" in headers["Accept"]


def test_response_size_cap_enforced(monkeypatch):
    _patch(monkeypatch, response=EndlessResponse(chunk=1024))
    with pytest.raises(ExtractionError) as exc:
        fetch_url_text("https://example.org/huge", max_bytes=1024 * 1024,
                       deadline_seconds=60)
    assert exc.value.detail == "response_too_large"


def test_slow_drip_hits_total_deadline(monkeypatch):
    class FakeTime:
        def __init__(self):
            self.now = 1000.0

        def monotonic(self):
            self.now += 0.2  # every loop iteration costs 0.2s of fake time
            return self.now

    monkeypatch.setattr(fetch_mod.time, "monotonic", FakeTime().monotonic)
    _patch(monkeypatch, response=EndlessResponse(chunk=1))
    with pytest.raises(OperationTimeoutError) as exc:
        fetch_url_text("https://example.org/drip", max_bytes=1024 * 1024,
                       deadline_seconds=1.0)
    assert exc.value.detail == "slow_response_deadline"


@pytest.mark.parametrize("code,detail", [
    (404, "http_404"),
    (403, "http_403"),
    (429, "http_429"),
    (503, "http_503"),
])
def test_http_errors_classified_by_status(monkeypatch, code, detail):
    err = urllib.error.HTTPError("https://example.org/x", code, "err", {}, None)
    _patch(monkeypatch, error=err)
    with pytest.raises(PageUnavailableError) as exc:
        fetch_url_text("https://example.org/x")
    assert exc.value.detail == detail


def test_timeout_is_operation_timeout(monkeypatch):
    _patch(monkeypatch, error=urllib.error.URLError("timed out"))
    with pytest.raises(OperationTimeoutError):
        fetch_url_text("https://example.org/slow")


def test_policy_blocks_propagate_unchanged(monkeypatch):
    """The SSRF guard must never be swallowed into a generic fetch error."""
    _patch(monkeypatch, error=SecurityBlockedError("private target", detail="ssrf"))
    with pytest.raises(SecurityBlockedError):
        fetch_url_text("http://169.254.169.254/latest/meta-data/")
    _patch(monkeypatch, error=ValueError("only public HTTP(S) URLs are allowed"))
    with pytest.raises(ValueError):
        fetch_url_text("http://127.0.0.1/")


def test_other_network_errors_stay_page_unavailable(monkeypatch):
    _patch(monkeypatch, error=urllib.error.URLError("connection reset"))
    with pytest.raises(PageUnavailableError):
        fetch_url_text("https://example.org/x")


# ---------------------------------------------- engine-optional SERP fetch
class NoEngineCtx:
    config = None

    def engine(self):
        raise AssertionError("browser engine must not be required (D-076)")


class FakeEngine:
    def __init__(self, html="<html><p>rendered</p></html>"):
        self.html = html
        self.navigated = []

    def navigate(self, url, **kw):
        self.navigated.append(url)

    def evaluate_js(self, expr):
        return self.html


class EngineCtx:
    config = None

    def __init__(self, engine):
        self._engine = engine

    def engine(self):
        return self._engine


def test_serp_fetch_works_without_browser_engine(monkeypatch):
    from iralens.search.engines.base import fetch_serp_html

    _patch(monkeypatch, response=FakeResponse(b"<html>serp results</html>"))
    html = fetch_serp_html(NoEngineCtx(), "https://example.org/serp", 10)
    assert "serp results" in html


def test_serp_fetch_falls_back_to_rendered_when_direct_fails(monkeypatch):
    from iralens.search.engines.base import fetch_serp_html

    _patch(monkeypatch, error=urllib.error.URLError("connection refused"))
    engine = FakeEngine()
    html = fetch_serp_html(EngineCtx(engine), "https://example.org/serp", 10)
    assert "rendered" in html and engine.navigated


def test_serp_fetch_raises_direct_error_when_engine_unavailable(monkeypatch):
    from iralens.errors import EngineUnavailableError
    from iralens.search.engines.base import fetch_serp_html

    class BrokenCtx:
        config = None

        def engine(self):
            raise EngineUnavailableError("engine not installed")

    _patch(monkeypatch, error=urllib.error.URLError("connection refused"))
    with pytest.raises(PageUnavailableError):
        fetch_serp_html(BrokenCtx(), "https://example.org/serp", 10)


def test_serp_block_page_triggers_rendered_fallback(monkeypatch):
    from iralens.search.engines.base import fetch_serp_html

    blocked = b"<html>unusual traffic captcha</html>"
    _patch(monkeypatch, response=FakeResponse(blocked))
    engine = FakeEngine("<html><p>real results</p></html>")
    html = fetch_serp_html(EngineCtx(engine), "https://example.org/serp", 10)
    assert "real results" in html and engine.navigated


def test_serp_policy_block_never_falls_back(monkeypatch):
    from iralens.search.engines.base import fetch_serp_html

    _patch(monkeypatch, error=SecurityBlockedError("private target", detail="ssrf"))
    engine = FakeEngine()
    with pytest.raises(SecurityBlockedError):
        fetch_serp_html(EngineCtx(engine), "http://10.0.0.1/serp", 10)
    assert engine.navigated == []
