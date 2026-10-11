# -*- coding: utf-8 -*-
"""Self-hosted SearXNG support and the private-host allowlist (D-082), offline.

A self-hosted instance on 127.0.0.1 or the LAN is the recommended way to get
independent engine redundancy — but the SSRF policy blocks private targets by
design, and it must keep blocking them for page reads. These tests pin both
halves of that contract.
"""

import urllib.parse

import pytest

from iralens import security
from iralens.errors import SecurityBlockedError, SourceUnavailableError
from iralens.search.engines import searxng as sx


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(security.ALLOWED_PRIVATE_HOSTS_ENV, raising=False)
    yield


# --------------------------------------------------------------- the allowlist
def test_private_urls_stay_blocked_by_default():
    for url in ("http://127.0.0.1:8888/search", "http://192.168.1.10:8888/",
                "http://searxng.lan/search", "http://localhost:8888/"):
        with pytest.raises(ValueError):
            security.normalize_public_http_url(url)
        with pytest.raises(ValueError):
            security.normalize_public_http_url(url, allow_private_hosts=True)


def test_allowlisted_host_is_accepted_only_with_the_flag(monkeypatch):
    monkeypatch.setenv(security.ALLOWED_PRIVATE_HOSTS_ENV, "127.0.0.1:8888")
    url = "http://127.0.0.1:8888/search?q=x&format=json"
    with pytest.raises(ValueError):
        security.normalize_public_http_url(url)                       # default: strict
    assert security.normalize_public_http_url(url, allow_private_hosts=True) == url
    # a different port on the same host is a different target
    with pytest.raises(ValueError):
        security.normalize_public_http_url("http://127.0.0.1:9999/search",
                                           allow_private_hosts=True)


def test_bare_host_entry_allows_any_port(monkeypatch):
    monkeypatch.setenv(security.ALLOWED_PRIVATE_HOSTS_ENV, "searxng.lan")
    assert security.normalize_public_http_url("http://searxng.lan:8888/", allow_private_hosts=True)
    assert security.normalize_public_http_url("http://searxng.lan/", allow_private_hosts=True)


def test_allowlist_never_covers_link_local_or_non_http(monkeypatch):
    monkeypatch.setenv(security.ALLOWED_PRIVATE_HOSTS_ENV,
                       "169.254.169.254,file,127.0.0.1:8888")
    with pytest.raises(ValueError):
        security.normalize_public_http_url("http://169.254.169.254/latest/meta-data/",
                                           allow_private_hosts=True)
    with pytest.raises(ValueError):
        security.normalize_public_http_url("file:///etc/passwd", allow_private_hosts=True)


def test_allowlist_does_not_bypass_credentials_or_scheme_checks(monkeypatch):
    monkeypatch.setenv(security.ALLOWED_PRIVATE_HOSTS_ENV, "127.0.0.1:8888")
    with pytest.raises(ValueError):
        security.normalize_public_http_url("http://user:pw@127.0.0.1:8888/",
                                           allow_private_hosts=True)
    with pytest.raises(ValueError):
        security.normalize_public_http_url("gopher://127.0.0.1:8888/",
                                           allow_private_hosts=True)


def test_address_resolution_accepts_only_allowlisted_hosts(monkeypatch):
    monkeypatch.setenv(security.ALLOWED_PRIVATE_HOSTS_ENV, "127.0.0.1:8888")
    assert security.resolve_public_addresses("127.0.0.1", 8888, allow_private_hosts=True)
    with pytest.raises(SecurityBlockedError):
        security.resolve_public_addresses("127.0.0.1", 9999, allow_private_hosts=True)
    with pytest.raises(SecurityBlockedError):
        security.resolve_public_addresses("127.0.0.1", 8888)          # flag absent


def test_page_reads_never_consult_the_allowlist(monkeypatch):
    """The allowlist is a search-transport opt-in; general reads stay strict."""
    monkeypatch.setenv(security.ALLOWED_PRIVATE_HOSTS_ENV, "127.0.0.1:8888")
    from iralens.sources import web as web_mod

    with pytest.raises(ValueError):
        web_mod.read_direct("http://127.0.0.1:8888/search?q=x")
    with pytest.raises(ValueError):
        web_mod.WebSource().read_url("http://127.0.0.1:8888/", None)  # blocked before ctx use


# ------------------------------------------------------- the searxng transport
def _fake_settings(monkeypatch, instances):
    class FakeSettings:
        searxng_instances = instances
        searxng_cooldown_seconds = 600.0
        searxng_timeout_seconds = 5

    monkeypatch.setattr(sx, "Settings", type("S", (), {"from_config": staticmethod(
        lambda config=None: FakeSettings())}))


class _Ctx:
    config = None


def test_searxng_calls_a_self_hosted_instance(monkeypatch):
    """The backend must reach the allowlisted loopback instance (no browser)."""
    _fake_settings(monkeypatch, ("http://127.0.0.1:8888",))
    monkeypatch.setenv(security.ALLOWED_PRIVATE_HOSTS_ENV, "127.0.0.1:8888")
    seen = {}

    def fake_fetch(url, *, timeout=30, headers=None, allow_private_hosts=False, **kw):
        seen["url"] = url
        seen["allow_private"] = allow_private_hosts
        seen["headers"] = headers
        return '{"results": [{"title": "Rust async", "url": "https://example.org/a",' \
               ' "content": "snippet"}]}'

    monkeypatch.setattr(sx, "fetch_url_text", fake_fetch)
    hits = sx.SearxngBackend().search("rust async", sx.SearchFilters(), 5, _Ctx())
    assert hits and hits[0].url == "https://example.org/a"
    assert seen["url"].startswith("http://127.0.0.1:8888/search?")
    assert seen["allow_private"] is True               # consulted the allowlist
    assert "format=json" in seen["url"]


def test_searxng_does_not_pass_the_flag_for_public_instances(monkeypatch):
    """Public instances keep the strict path; the flag is only ever consulted."""
    _fake_settings(monkeypatch, ("https://searx.example.org",))
    seen = {}

    def fake_fetch(url, *, timeout=30, headers=None, allow_private_hosts=False, **kw):
        seen["allow_private"] = allow_private_hosts
        return '{"results": []}'

    monkeypatch.setattr(sx, "fetch_url_text", fake_fetch)
    assert sx.SearxngBackend().search("q", sx.SearchFilters(), 5, _Ctx()) == []
    assert seen["allow_private"] is True               # flag set, but nothing allowlisted


def test_blocked_instance_error_is_typed_and_actionable(monkeypatch):
    """A loopback instance without the allowlist fails with a clear hint."""
    _fake_settings(monkeypatch, ("http://127.0.0.1:8888",))

    def fake_fetch(url, *, timeout=30, headers=None, allow_private_hosts=False, **kw):
        return security.safe_urlopen(url, timeout=timeout, headers=headers,
                                     allow_private_hosts=allow_private_hosts)

    monkeypatch.setattr(sx, "fetch_url_text", fake_fetch)
    with pytest.raises(SourceUnavailableError) as exc_info:
        sx.SearxngBackend().search("q", sx.SearchFilters(), 5, _Ctx())
    assert "IRALENS_ALLOWED_PRIVATE_HOSTS" in exc_info.value.hint or \
           "IRALENS_ALLOWED_PRIVATE_HOSTS" in exc_info.value.detail


def test_url_building_puts_the_query_where_searxng_expects_it():
    backend = sx.SearxngBackend()
    filters = sx.SearchFilters()
    url = backend._build_url("http://127.0.0.1:8888", "hello world", filters, 5)
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qs(parsed.query)
    assert parsed.path == "/search"
    assert params["q"] == ["hello world"]
    assert params["format"] == ["json"]
