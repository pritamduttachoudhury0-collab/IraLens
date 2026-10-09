# -*- coding: utf-8 -*-
"""Redirect validation, DNS pinning, and the hardened fetch path.

Live hosts are never contacted: DNS resolution is simulated with
monkeypatched getaddrinfo, and the "remote" server is a localhost
http.server that the pinned-connection code is pointed at directly. That
exercises the real redirect/SSRF code paths without any network access.
"""

import http.server
import socket
import threading
import urllib.error

import pytest

from halfiralens import security
from halfiralens.errors import SecurityBlockedError
from halfiralens.security import (
    MAX_REDIRECTS,
    SafeRedirectHandler,
    is_public_address,
    resolve_public_addresses,
    safe_urlopen,
)


# --------------------------------------------------------- address checks
def test_is_public_address():
    assert is_public_address("93.184.216.34")
    assert is_public_address("2606:2800:220:1::1")
    for bad in ("127.0.0.1", "10.1.2.3", "192.168.0.1", "169.254.169.254",
                "::1", "fc00::1", "fe80::1", "0.0.0.0", "not-an-ip"):
        assert not is_public_address(bad), bad


def test_resolve_refuses_private_only_answers(monkeypatch):
    def fake_getaddrinfo(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SecurityBlockedError):
        resolve_public_addresses("rebind.example", 80)


def test_resolve_refuses_mixed_public_private_answers(monkeypatch):
    """A mixed answer is a rebinding signature; connect to nothing."""
    def fake_getaddrinfo(host, port, type=None):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", 80)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SecurityBlockedError):
        resolve_public_addresses("rebind.example", 80)


def test_resolve_returns_only_public_addresses(monkeypatch):
    def fake_getaddrinfo(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve_public_addresses("good.example", 80) == ["93.184.216.34"]


def test_resolve_failure_is_value_error(monkeypatch):
    def fake_getaddrinfo(host, port, type=None):
        raise socket.gaierror("no such name")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError):
        resolve_public_addresses("nope.example", 80)


# ------------------------------------------------- redirect policy (unit)
class _FakeReq:
    def __init__(self, url, redirect_dict=None):
        self._url = url
        self.redirect_dict = redirect_dict or {}

    def get_full_url(self):
        return self._url

    def get_method(self):
        return "GET"

    def has_header(self, name):
        return False

    def get_header(self, name, default=None):
        return default

    def add_unredirected_header(self, key, val):
        pass


def test_redirect_to_private_target_rejected():
    handler = SafeRedirectHandler()
    req = _FakeReq("https://public.example/start")
    with pytest.raises(SecurityBlockedError):
        handler.redirect_request(req, None, 302, "Found", {},
                                 "http://169.254.169.254/latest/meta-data/")
    with pytest.raises(SecurityBlockedError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://10.0.0.5/")
    with pytest.raises(SecurityBlockedError):
        handler.redirect_request(req, None, 302, "Found", {}, "file:///etc/passwd")


def test_https_to_http_downgrade_rejected():
    handler = SafeRedirectHandler()
    req = _FakeReq("https://public.example/start")
    with pytest.raises(SecurityBlockedError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://public.example/x")


def test_redirect_chain_length_capped():
    handler = SafeRedirectHandler()
    req = _FakeReq("https://public.example/start",
                    redirect_dict={f"https://h{i}.example/": 1 for i in range(MAX_REDIRECTS)})
    with pytest.raises(SecurityBlockedError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://public.example/next")


# ------------------------------- end-to-end with a local server + fake DNS
class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == "/ok":
            body = b"public content"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect-ok":
            self.send_response(302)
            self.send_header("Location", f"http://fake-public.test:{port}/ok")
            self.end_headers()
        elif self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        elif self.path in ("/loop-a", "/loop-b"):
            other = "/loop-b" if self.path == "/loop-a" else "/loop-a"
            self.send_response(302)
            self.send_header("Location", f"http://fake-public.test:{port}{other}")
            self.end_headers()
        elif self.path == "/self-loop":
            self.send_response(302)
            self.send_header("Location", f"http://fake-public.test:{port}/self-loop")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server(monkeypatch):
    """Local HTTP server standing in for a public host.

    Only the *predicate* ("is this resolved address global?") is stubbed to
    accept the loopback address; resolution, redirect re-validation, the hop
    cap, and the pinned connections all run for real. Private redirect targets
    are still rejected by the literal-IP check in normalize_public_http_url,
    which does not involve the stub at all.
    """
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port_arg, *args, **kwargs):
        if host == "fake-public.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real_getaddrinfo(host, port_arg, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(security, "is_public_address", lambda address: True)
    yield port
    server.shutdown()


def test_safe_urlopen_follows_public_redirect(local_server):
    with safe_urlopen(f"http://fake-public.test:{local_server}/redirect-ok", timeout=5) as resp:
        assert resp.status == 200
        assert resp.read() == b"public content"


def test_safe_urlopen_blocks_redirect_to_private_target(local_server):
    with pytest.raises(SecurityBlockedError):
        safe_urlopen(f"http://fake-public.test:{local_server}/redirect-private", timeout=5)


def test_safe_urlopen_caps_redirect_chains(local_server):
    """Alternating hops grow the chain; the MAX_REDIRECTS cap must fire."""
    with pytest.raises(SecurityBlockedError):
        safe_urlopen(f"http://fake-public.test:{local_server}/loop-a", timeout=5)


def test_self_loop_fails_as_http_error(local_server):
    """A same-URL loop trips urllib's own repeat cap: an HTTPError, which the
    reader layer classifies into a redirect-loop PageUnavailableError."""
    with pytest.raises(urllib.error.HTTPError):
        safe_urlopen(f"http://fake-public.test:{local_server}/self-loop", timeout=5)
