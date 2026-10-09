# -*- coding: utf-8 -*-
"""Hardened static reader: HTTP classification, soft 404s, size caps,
slow-drip deadlines, and consistent max_chars enforcement (D-071).

Everything is offline: safe_urlopen and the response stream are fakes.
"""

import io
import urllib.error

import pytest

from iralens import content_guard
from iralens.errors import (
    ExtractionError,
    OperationTimeoutError,
    PageUnavailableError,
)
from iralens.sources import web as web_mod


class FakeResponse:
    """Serve a real body in chunks of at most *chunk* bytes per read()."""

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
    """Infinite stream: one *chunk* per read(), forever (slow-drip/size tests)."""

    def __init__(self, chunk: int = 1, fill: bytes = b"a"):
        self._chunk = chunk
        self._fill = fill

    def read(self, n=-1):
        return self._fill * self._chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, response=None, error=None):
    def fake_urlopen(url, *, timeout=30, headers=None):
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(web_mod, "safe_urlopen", fake_urlopen)


# ------------------------------------------------------- HTTP classification
@pytest.mark.parametrize("code,expected_type,detail", [
    (404, PageUnavailableError, "http_404"),
    (403, PageUnavailableError, "http_403"),
    (401, PageUnavailableError, "http_401"),
    (429, PageUnavailableError, "http_429"),
    (500, PageUnavailableError, "http_500"),
    (503, PageUnavailableError, "http_503"),
])
def test_http_errors_are_classified_by_status(monkeypatch, code, expected_type, detail):
    err = urllib.error.HTTPError("https://r.jina.ai/x", code, "status", {}, io.BytesIO(b""))
    _patch_urlopen(monkeypatch, error=err)
    with pytest.raises(expected_type) as exc_info:
        web_mod.read_with_static_reader("https://example.org/x")
    assert exc_info.value.detail == detail
    assert str(code) in exc_info.value.message


def test_403_message_mentions_bot_check(monkeypatch):
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))
    _patch_urlopen(monkeypatch, error=err)
    with pytest.raises(PageUnavailableError) as exc_info:
        web_mod.read_with_static_reader("https://example.org/x")
    assert "bot check" in exc_info.value.message or "denied" in exc_info.value.message
    assert exc_info.value.hint  # the caller gets a next step, not a dead end


def test_redirect_loop_surfaces_as_redirect_loop_error(monkeypatch):
    err = urllib.error.HTTPError("u", 302, "Found", {}, io.BytesIO(b""))
    _patch_urlopen(monkeypatch, error=err)
    with pytest.raises(PageUnavailableError) as exc_info:
        web_mod.read_with_static_reader("https://example.org/loopy")
    assert exc_info.value.detail == "redirect_loop"


def test_generic_network_error_stays_page_unavailable(monkeypatch):
    _patch_urlopen(monkeypatch, error=urllib.error.URLError("connection reset"))
    with pytest.raises(PageUnavailableError):
        web_mod.read_with_static_reader("https://example.org/x")


def test_security_blocked_error_propagates(monkeypatch):
    from iralens.errors import SecurityBlockedError

    _patch_urlopen(monkeypatch, error=SecurityBlockedError("redirect to private target"))
    with pytest.raises(SecurityBlockedError):
        web_mod.read_with_static_reader("https://example.org/x")


# ------------------------------------------------------------ soft 404/empty
def test_soft_404_detected(monkeypatch):
    body = b"Title: 404 Not Found\n\nThe page you requested does not exist."
    _patch_urlopen(monkeypatch, response=FakeResponse(body))
    with pytest.raises(PageUnavailableError) as exc_info:
        web_mod.read_with_static_reader("https://example.org/gone")
    assert exc_info.value.detail == "soft_404"


def test_long_page_mentioning_404_is_not_soft_404(monkeypatch):
    body = ("Title: How 404 pages work\n\n" + ("A page not found is an error. " * 200)).encode()
    _patch_urlopen(monkeypatch, response=FakeResponse(body))
    text = web_mod.read_with_static_reader("https://example.org/article")
    assert "404" in text  # served normally: the length bound saved it


def test_empty_response_is_reported(monkeypatch):
    _patch_urlopen(monkeypatch, response=FakeResponse(b""))
    with pytest.raises(PageUnavailableError) as exc_info:
        web_mod.read_with_static_reader("https://example.org/blank")
    assert exc_info.value.detail == "empty_response"


def test_antibot_challenge_is_classified(monkeypatch):
    body = ("Warning: r.jina.ai is requiring captcha for access to this page.\n"
            "Title: Just a moment...\n## Performing security verification").encode()
    _patch_urlopen(monkeypatch, response=FakeResponse(body))
    with pytest.raises(PageUnavailableError) as exc_info:
        web_mod.read_with_static_reader("https://example.org/walled")
    assert exc_info.value.detail == "antibot_challenge"


# ------------------------------------------------------ size cap and deadline
def test_response_size_cap_enforced(monkeypatch):
    _patch_urlopen(monkeypatch, response=EndlessResponse(chunk=256 * 1024))
    with pytest.raises(ExtractionError) as exc_info:
        web_mod.read_with_static_reader("https://example.org/huge", max_bytes=1024 * 1024)
    assert exc_info.value.detail == "response_too_large"


def test_size_cap_defaults_to_5mb():
    assert web_mod._DEFAULT_MAX_BYTES == 5 * 1024 * 1024


def test_slow_drip_hits_total_deadline(monkeypatch):
    """One byte per 'read' under a fake clock: the overall deadline fires."""

    class FakeTime:
        def __init__(self):
            self.now = 1000.0

        def monotonic(self):
            self.now += 0.2  # every loop iteration costs 0.2s of fake time
            return self.now

    monkeypatch.setattr(web_mod.time, "monotonic", FakeTime().monotonic)
    _patch_urlopen(monkeypatch, response=EndlessResponse(chunk=1))
    with pytest.raises(OperationTimeoutError) as exc_info:
        web_mod.read_with_static_reader("https://example.org/drip",
                                        deadline_seconds=1.0)
    assert exc_info.value.detail == "slow_response_deadline"


def test_normal_read_completes_under_deadline(monkeypatch):
    body = b"# Title\n\n" + b"x" * 1000
    _patch_urlopen(monkeypatch, response=FakeResponse(body, chunk=128))
    text = web_mod.read_with_static_reader("https://example.org/p", deadline_seconds=60)
    assert text.startswith("# Title")


# ------------------------------------------------------ max_chars enforcement
class Ctx:
    config = None

    def engine(self):
        raise AssertionError("browser backend should not be used in these tests")


def _src(monkeypatch, tmp_path, body="# Deep page\n\n" + ("content line\n" * 500)):
    from iralens.cache import ResponseCache

    _patch_urlopen(monkeypatch, response=FakeResponse(body.encode()))
    monkeypatch.setattr(web_mod, "ResponseCache",
                        lambda enabled=True: ResponseCache(tmp_path / "pages"))
    return web_mod.WebSource()


def test_read_url_enforces_max_chars_and_marks_truncation(monkeypatch, tmp_path):
    src = _src(monkeypatch, tmp_path)
    art = src.read_url("https://example.org/p", Ctx(), mode="static", max_chars=200)
    assert len(art.content) <= 250  # budget plus the truncation marker
    assert art.metadata["truncated"] is True
    assert art.metadata["max_chars"] == 200
    assert "truncated" in art.content


def test_read_url_small_page_not_marked_truncated(monkeypatch, tmp_path):
    src = _src(monkeypatch, tmp_path, body="# Small\n\nshort.")
    art = src.read_url("https://example.org/p", Ctx(), mode="static", max_chars=20000)
    assert art.metadata["truncated"] is False
    assert art.content.startswith("# Small")


def test_cached_full_text_serves_different_budgets(monkeypatch, tmp_path):
    src = _src(monkeypatch, tmp_path)
    small = src.read_url("https://example.org/p", Ctx(), mode="static", max_chars=100)
    big = src.read_url("https://example.org/p", Ctx(), mode="static", max_chars=400)
    assert small.provenance["cache"]["status"] == "miss"
    assert big.provenance["cache"]["status"] == "hit"
    assert len(big.content) > len(small.content)
    assert big.metadata["truncated"] is True


def test_browser_path_receives_max_chars(monkeypatch, tmp_path):
    seen = {}

    class FakeEngine:
        def navigate(self, url, **kw):
            pass

        def markdown(self, max_chars=0):
            seen["max_chars"] = max_chars
            return "# B\n\nbody"

        def current_url_title(self):
            return {"url": "https://example.org/p", "title": "B"}

    class BrowserCtx:
        config = None

        def engine(self):
            return FakeEngine()

    src = web_mod.WebSource()
    src.read_url("https://example.org/p", BrowserCtx(), mode="browser", max_chars=777)
    assert seen["max_chars"] == 777


# ------------------------------------------------------------ truncate helper
def test_truncate_text_reports_omitted_chars():
    text, truncated = content_guard.truncate_text("a" * 1000, 100)
    assert truncated is True
    assert text.startswith("a" * 100)
    assert "900 chars" in text
    same, not_truncated = content_guard.truncate_text("short", 100)
    assert same == "short" and not_truncated is False
