# -*- coding: utf-8 -*-
"""Regression tests for the SSRF hole in the URL-taking entry points.

Before the patch, ``HttpClient`` fetched whatever URL it was handed and
followed redirects blindly, so ``GET /api/read?url=http://169.254.169.254/...``
made the *server* read its own cloud credentials and return them.

These tests need no network: the guard must refuse the target before a
request is ever attempted.
"""
from __future__ import annotations

import asyncio

import pytest

from pynewsbrowser.safeurl import (
    SafeRedirects, UnsafeURLError, is_public_http_url, normalize_public_http_url,
)

METADATA = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


@pytest.mark.parametrize("url", [
    METADATA,
    "http://127.0.0.1:6379/",
    "http://localhost/admin",
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://2130706433/",          # 127.0.0.1 as an integer
    "http://127.1/",               # short-form loopback
    "http://0177.0.0.1/",          # octal loopback
    "file:///etc/passwd",
    "javascript:alert(1)",
    "gopher://example.test/",
    "http://user:pass@example.test/",
    "http://searxng.local:8888/",
    "http://intranet/",
])
def test_private_and_non_http_targets_are_refused(url):
    assert not is_public_http_url(url)
    with pytest.raises(UnsafeURLError):
        normalize_public_http_url(url)


@pytest.mark.parametrize("url", [
    "https://assamtribune.com/floods",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "http://example.com/a?b=1",
])
def test_ordinary_public_urls_still_work(url):
    assert normalize_public_http_url(url) == url


def test_http_client_refuses_before_making_a_request():
    """The guard must sit in front of the transport, not behind it."""
    from pynewsbrowser.config import Settings
    from pynewsbrowser.http import HttpClient

    class ExplodingClient:
        async def get(self, *a, **k):
            raise AssertionError("a request was attempted for a blocked target")

    async def run():
        client = HttpClient(Settings(), client=ExplodingClient())
        with pytest.raises(UnsafeURLError):
            await client.get(METADATA, use_cache=False, respect_robots=False)

    asyncio.run(run())


def test_redirect_to_a_private_target_is_refused():
    """The hop that defeats a first-URL-only check.

    A public host answers 302 -> cloud metadata. Following it blindly makes
    the guard decorative.
    """
    class RedirectingClient:
        def __init__(self):
            self.calls = []

        async def get(self, url, **kwargs):
            self.calls.append(url)

            class Resp:
                status_code = 302
                headers = {"location": METADATA}

            return Resp()

    async def run():
        client = RedirectingClient()
        with pytest.raises(UnsafeURLError):
            await SafeRedirects(client).get("https://example.test/start")
        # It reached the public URL, then refused to follow the private hop.
        assert client.calls == ["https://example.test/start"]

    asyncio.run(run())


def test_relative_redirect_is_resolved_then_revalidated():
    class Client:
        def __init__(self):
            self.calls = []

        async def get(self, url, **kwargs):
            self.calls.append(url)

            class Resp:
                def __init__(self, status, headers):
                    self.status_code = status
                    self.headers = headers

            if len(self.calls) == 1:
                return Resp(302, {"location": "/next"})
            return Resp(200, {})

    async def run():
        client = Client()
        resp = await SafeRedirects(client).get("https://example.test/start")
        assert resp.status_code == 200
        assert client.calls == ["https://example.test/start", "https://example.test/next"]

    asyncio.run(run())


def test_redirect_loop_is_bounded():
    class LoopingClient:
        async def get(self, url, **kwargs):
            class Resp:
                status_code = 302
                headers = {"location": "https://example.test/again"}

            return Resp()

    async def run():
        with pytest.raises(UnsafeURLError):
            await SafeRedirects(LoopingClient(), max_hops=3).get("https://example.test/start")

    asyncio.run(run())


def test_learned_operation_cannot_target_a_private_host():
    from pynewsbrowser.config import Settings
    from pynewsbrowser.http import HttpClient
    from pynewsbrowser.newsops.learn import learn_json_search

    async def run():
        async with HttpClient(Settings()) as client:
            with pytest.raises(UnsafeURLError):
                await learn_json_search(
                    "evil", "http://169.254.169.254/latest/{query}",
                    [{"query": "x", "items": []}], client,
                )

    asyncio.run(run())
