# -*- coding: utf-8 -*-
"""The standalone SSRF guard shipped in contrib/pynewsbrowser.

That module is a fix offered to the upstream PyNewsBrowser project, whose
`/api/read?url=` endpoint and learned URL templates fetched any caller-supplied
URL with `follow_redirects=True` — an open proxy to cloud metadata and the
server's own private network.

It is not on IraLens's runtime path, but it is security code shipped from this
repository, so it is tested here too. It is loaded by file path because
`contrib/` is deliberately not an installed package.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "contrib" / "pynewsbrowser" / "safeurl.py"
_spec = importlib.util.spec_from_file_location("contrib_safeurl", _MODULE_PATH)
safeurl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(safeurl)

normalize = safeurl.normalize_public_http_url
UnsafeURLError = safeurl.UnsafeURLError


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://127.0.0.1:6379/",
    "http://localhost/admin",
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://2130706433/",
    "http://127.1/",
    "http://0177.0.0.1/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "gopher://example.test/",
    "http://user:pass@example.test/",
    "http://searxng.local:8888/",
    "http://intranet/",
    "",
    "   ",
    "http://example.test/\r\nX-Injected: 1",
    "http://example.test/a b",
])
def test_unsafe_targets_are_refused(url):
    with pytest.raises(UnsafeURLError):
        normalize(url)


@pytest.mark.parametrize("url", [
    "https://assamtribune.com/floods",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "http://example.com/a?b=1",
])
def test_public_urls_are_preserved(url):
    assert normalize(url) == url


def test_trailing_whitespace_is_normalized_away_not_passed_through():
    # A trailing newline is stripped, so the URL actually fetched is clean.
    # An *interior* CRLF is a header-injection shape and is refused above.
    assert normalize("http://example.test/\n") == "http://example.test/"


def test_guard_agrees_with_iralens_own_policy():
    """The port must not be weaker than the policy it was ported from."""
    from iralens.security import normalize_public_http_url as iralens_normalize

    for url in ("http://169.254.169.254/", "http://127.0.0.1/", "http://10.1.2.3/",
                "file:///etc/passwd", "http://user:pass@example.test/"):
        with pytest.raises(UnsafeURLError):
            normalize(url)
        with pytest.raises(ValueError):
            iralens_normalize(url)


def test_metadata_dns_shape_is_refused_by_the_dns_layer():
    """Text validation cannot see a rebinding name; the DNS layer must.

    `169.254.169.254.xip.io` is a syntactically valid public hostname, so the
    text guard passes it — exactly as IraLens's own text guard does. The
    resolution check is what refuses it.
    """
    assert normalize("http://169.254.169.254.xip.io/").startswith("http://")
    assert safeurl.resolve_is_public("169.254.169.254") is False
    assert safeurl.resolve_is_public("localhost") is False


def test_redirect_hop_to_a_private_target_is_refused():
    import asyncio

    class RedirectingClient:
        def __init__(self):
            self.calls = []

        async def get(self, url, **kwargs):
            self.calls.append(url)

            class Resp:
                status_code = 302
                headers = {"location": "http://169.254.169.254/latest/meta-data/"}

            return Resp()

    async def run():
        client = RedirectingClient()
        with pytest.raises(UnsafeURLError):
            await safeurl.SafeRedirects(client).get("https://example.test/start")
        assert client.calls == ["https://example.test/start"]

    asyncio.run(run())


def test_redirect_loop_is_bounded():
    import asyncio

    class LoopingClient:
        async def get(self, url, **kwargs):
            class Resp:
                status_code = 302
                headers = {"location": "https://example.test/again"}

            return Resp()

    async def run():
        with pytest.raises(UnsafeURLError):
            await safeurl.SafeRedirects(LoopingClient(), max_hops=3).get(
                "https://example.test/start")

    asyncio.run(run())
