# -*- coding: utf-8 -*-
"""F-05 and F-06: documentation must agree with the code it describes.

F-05: `docs/search.md` documented the search-cache TTL as 900 seconds while
      `Settings.cache_search_ttl_seconds` is 300. A threefold error in the
      documented freshness window.
F-06: `docs/security.md` and `KNOWN_LIMITATIONS.md` said RSS/Atom feeds use
      their own HTTP client and do not pin DNS answers, while
      `iralens/sources/rss.py` uses `security.safe_urlopen` (DNS-pinned,
      redirect-revalidated). The claim was stale, not a vulnerability.

Both are checked here against the running code, so the docs cannot drift
silently again.
"""

from __future__ import annotations

import re
from pathlib import Path

from iralens import sources
from iralens.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------- F-05
def test_documented_search_cache_ttl_matches_settings():
    """The number in docs/search.md must equal the runtime default."""
    doc = _read("docs/search.md")
    documented = re.search(r"`cache_search_ttl_seconds`\s*\((\d+)\)", doc)
    assert documented, "docs/search.md no longer states the search-cache TTL"
    assert int(documented.group(1)) == Settings.cache_search_ttl_seconds


def test_search_cache_ttl_default_is_300_seconds():
    """Pins the value itself: the doc could be changed to match a bad default."""
    assert Settings.cache_search_ttl_seconds == 300


def test_documented_page_cache_ttl_matches_settings():
    doc = _read("docs/search.md")
    documented = re.search(r"`cache_page_ttl_seconds`\s*\((\d+)\)", doc)
    assert documented, "docs/search.md no longer states the page-cache TTL"
    assert int(documented.group(1)) == Settings.cache_page_ttl_seconds


def test_no_other_document_states_a_stale_search_ttl():
    """No doc may quote a search TTL that is not the configured default."""
    configured = str(Settings.cache_search_ttl_seconds)
    offenders = []
    for path in ("README.md", "ARCHITECTURE.md", "DECISIONS.md", "KNOWN_LIMITATIONS.md",
                 "docs/search.md", "docs/getting-started.md", "docs/api.md"):
        text = _read(path)
        for match in re.finditer(r"cache_search_ttl_seconds\D{0,12}(\d{2,6})", text):
            if match.group(1) != configured:
                offenders.append((path, match.group(1)))
    assert offenders == [], f"stale search TTL documented: {offenders}"


# --------------------------------------------------------------------- F-06
def test_rss_uses_the_pinned_transport():
    """The RSS fetch path must go through `safe_urlopen`, not a bare client."""
    src = (REPO_ROOT / "iralens" / "sources" / "rss.py").read_text(encoding="utf-8")
    assert "safe_urlopen" in src
    assert "normalize_public_http_url" in src
    # `requests` is the un-pinned client; RSS must not use it.
    assert not re.search(r"^\s*import requests\b", src, re.MULTILINE)
    assert not re.search(r"requests\.(get|post)\(", src)


def test_security_doc_says_rss_is_pinned():
    doc = _read("docs/security.md")
    assert "RSS" in doc
    assert "safe_urlopen" in doc
    # The stale claim: RSS "does not pin resolved addresses" via `requests`.
    assert "RSS feeds via `requests`" not in doc
    assert not re.search(r"RSS[^\n]*do(es)? not pin", doc)


def test_known_limitations_names_the_source_that_still_uses_its_own_client():
    doc = _read("KNOWN_LIMITATIONS.md")
    assert "safe_urlopen" in doc
    assert "transcribe" in doc
    # The stale claim listed RSS among the sources with their own HTTP client.
    assert not re.search(r"own HTTP clients? \(RSS", doc)


def test_the_documented_unpinned_source_really_uses_requests():
    """If the docs name `transcribe` as the un-pinned client, verify it."""
    src = (REPO_ROOT / "iralens" / "sources" / "transcribe.py").read_text(encoding="utf-8")
    assert re.search(r"^\s*import requests\b", src, re.MULTILINE)
    assert "normalize_public_http_url" in src


def test_every_source_transport_is_classified():
    """Every source module's transport is either pinned or the documented one.

    This is the guard that catches the *next* source that starts making its own
    HTTP calls without an update to the security documentation.
    """
    sources_dir = REPO_ROOT / "iralens" / "sources"
    unpinned = []
    for path in sorted(sources_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"requests\.(get|post)\(", text):
            unpinned.append(path.stem)
    assert unpinned == ["transcribe"], (
        f"sources using an un-pinned HTTP client changed ({unpinned}); "
        "update docs/security.md and KNOWN_LIMITATIONS.md to match")


def test_rss_source_health_and_fetch_use_the_safe_transport(monkeypatch):
    """Behavioural check: an RSS read goes through the guarded transport."""
    calls = []

    class FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body
            self._done = False

        def read(self, _n=-1):
            if self._done:
                return b""
            self._done = True
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def headers(self):
            return {"Content-Type": "application/rss+xml"}

        @property
        def status(self):
            return 200

        @property
        def url(self):
            return "https://feeds.example.org/rss"

    def fake_safe_urlopen(url, *args, **kwargs):
        calls.append(url)
        return FakeResponse(
            b'<?xml version="1.0"?><rss version="2.0"><channel>'
            b"<title>Feed</title><item><title>Post</title>"
            b"<link>https://feeds.example.org/p</link>"
            b"<description>Hello.</description></item></channel></rss>")

    monkeypatch.setattr(sources.rss, "safe_urlopen", fake_safe_urlopen)
    source = sources.rss.RSSSource()
    artifact = source.read_url("https://feeds.example.org/rss", _NullContext())

    assert calls == ["https://feeds.example.org/rss"], (
        "the RSS read did not go through safe_urlopen")
    assert artifact.title == "Feed"
    assert "Post" in artifact.content


class _NullContext:
    """A context that needs nothing for an RSS read."""

    class _Config:
        def get(self, key, default=None):
            return default

    def __init__(self) -> None:
        self.config = self._Config()

    def engine(self):
        raise AssertionError("an RSS read must not start the browser engine")


# --------------------------------------------------------- F-05 settings sanity
def test_settings_defaults_are_the_single_source_of_truth():
    """`Settings` must not hardcode a TTL that a module then overrides."""
    import inspect

    import iralens.settings as settings_mod

    src = inspect.getsource(settings_mod)
    declared = re.findall(r"cache_search_ttl_seconds:\s*int\s*=\s*(\d+)", src)
    assert declared == ["300"], declared
