# -*- coding: utf-8 -*-
"""A browser-shaped HTTP transport for the internal browser engine.

Why not ``urllib``
------------------
``urllib`` is fine at fetching a file and wrong at being a browser. It sends
``Connection: close`` on every request, it sends ``Accept-Encoding: identity``
unless told otherwise, it cannot keep a connection alive, it lowercases
header names in ways Chrome never does, and it always presents the TLS
fingerprint of the local OpenSSL build. Bot filters look at exactly these
things: the header *set*, the header *order*, the header *casing*, the
connection reuse pattern and the TLS handshake.

So this module speaks HTTP/1.1 the way Chrome does:

* one persistent connection per origin, reused across requests;
* the exact header set and ordering Chrome sends for a navigation or a
  subresource (:mod:`.stealth` builds it);
* ``Accept-Encoding`` advertising only the codecs this process can decode;
* transparent gzip/deflate/brotli/zstd decoding.

Optional TLS impersonation
--------------------------
The TLS handshake is the one thing the standard library cannot fake: a
Python/OpenSSL ClientHello is not a Chrome ClientHello. When ``curl_cffi``
is installed, the transport can hand the request to it, which reproduces
Chrome's TLS and HTTP/2 fingerprints. That is an *optional extra*
(``pip install "iralens[stealth]"``); without it the transport still works
and the engine reports the capability as unavailable rather than pretending.

What this module does not do
----------------------------
It does not retry a request behind a challenge, rotate identities, or send
one request from several source addresses. Those are policy decisions, and
they live in :mod:`.captcha` and :mod:`.runtime`, not here.
"""

from __future__ import annotations

import gzip
import http.client
import socket
import ssl
import urllib.parse
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Default request timeout, in seconds.
DEFAULT_TIMEOUT = 30.0

#: Cap on a single response body, mirroring the engine's navigation cap.
MAX_BODY_BYTES = 10 * 1024 * 1024


class TransportError(RuntimeError):
    """Base class for transport failures."""


class TransportTimeout(TransportError):
    """The request did not finish inside its deadline."""


class TransportNetworkError(TransportError):
    """The request could not be delivered (DNS, connect, reset, TLS)."""


def _brotli_module():
    """Return a brotli decompressor module, or ``None`` when unavailable."""
    for name in ("brotlicffi", "brotli"):
        try:
            module = __import__(name)
        except Exception:
            continue
        decompress = getattr(module, "decompress", None)
        if decompress is not None:
            return module
    return None


def _zstd_module():
    """Return a zstd decompressor module, or ``None`` when unavailable."""
    for name in ("zstandard", "pyzstd", "zstd"):
        try:
            module = __import__(name)
        except Exception:
            continue
        if name == "zstandard":
            return ("zstandard", module)
        return (name, module)
    # Python 3.14 ships compression.zstd in the standard library.
    try:  # pragma: no cover - depends on the interpreter version
        from compression import zstd as _stdlib_zstd  # type: ignore

        return ("stdlib", _stdlib_zstd)
    except Exception:
        return None


def supported_codings() -> List[str]:
    """The content codings this process can actually decode, best first.

    Only codecs that can be decoded are advertised. A server that answers
    with an encoding we cannot read is a broken page; advertising it is
    strictly worse than not asking for it.
    """
    codings = ["gzip", "deflate"]
    if _brotli_module() is not None:
        codings.append("br")
    if _zstd_module() is not None:
        codings.append("zstd")
    return codings


def accept_encoding_value() -> str:
    """The ``Accept-Encoding`` header value matching :func:`supported_codings`."""
    return ", ".join(supported_codings())


def _decompress(data: bytes, encoding: str) -> bytes:
    """Decode a response body according to its ``Content-Encoding``."""
    enc = (encoding or "").strip().lower()
    if not data or enc in ("", "identity"):
        return data
    try:
        if enc == "gzip":
            return gzip.decompress(data)
        if enc == "deflate":
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
        if enc == "br":
            module = _brotli_module()
            if module is not None:
                return module.decompress(data)
            return data
        if enc == "zstd":
            found = _zstd_module()
            if found is None:
                return data
            kind, module = found
            if kind == "zstandard":
                return module.ZstdDecompressor().decompress(data)
            if kind == "pyzstd":
                return module.decompress(data)
            if kind == "zstd":
                return module.decompress(data)
            return module.decompress(data)
    except Exception:
        # A body we cannot decode is returned untouched. The caller still
        # gets the bytes; it is better than losing the response entirely.
        return data
    return data


@dataclass
class TransportResponse:
    """One HTTP response, with its headers in wire order."""

    url: str
    status: int
    reason: str = ""
    headers: List[Tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    http_version: str = "HTTP/1.1"

    def header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """The first value of header *name* (case-insensitive)."""
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return default

    def header_list(self, name: str) -> List[str]:
        """Every value of header *name* (case-insensitive)."""
        lowered = name.lower()
        return [value for key, value in self.headers if key.lower() == lowered]

    @property
    def content_type(self) -> str:
        return self.header("Content-Type", "text/html") or "text/html"

    @property
    def content_encoding(self) -> str:
        return self.header("Content-Encoding", "") or ""

    def header_map(self) -> Dict[str, str]:
        """Headers as a dict (last value wins), for callers that need a map."""
        out: Dict[str, str] = {}
        for key, value in self.headers:
            out[key] = value
        return out


class HttpTransport:
    """Chrome-shaped HTTP/1.1 client with per-origin connection reuse.

    The transport is deliberately small: it performs one request at a time,
    never follows a redirect (the engine validates every hop itself), and
    never decides policy. ``http.client`` is used directly rather than
    ``urllib`` because ``urllib`` rewrites the header set: it forces
    ``Connection: close``, replaces ``Accept-Encoding`` with ``identity``,
    and capitalizes header names in its own way.
    """

    def __init__(
        self,
        *,
        proxy_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        accept_encoding: Optional[str] = None,
        tls_impersonation: bool = False,
        chrome_major: Optional[int] = None,
    ) -> None:
        self.proxy_url = proxy_url
        self.timeout = float(timeout)
        self.accept_encoding = accept_encoding or accept_encoding_value()
        self._pool: Dict[Tuple[str, str, int], http.client.HTTPConnection] = {}
        self._tls_impersonation = bool(tls_impersonation)
        self._curl_session: Any = None
        self._curl_error: Optional[str] = None
        self._curl_target = ""
        if self._tls_impersonation:
            self._curl_session, self._curl_error, self._curl_target = _make_curl_session(
                chrome_major
            )

    # ------------------------------------------------------------- capability
    @property
    def tls_impersonation(self) -> bool:
        """Whether TLS/HTTP2 fingerprint impersonation is active."""
        return self._curl_session is not None

    @property
    def tls_impersonation_note(self) -> str:
        """A one-line, honest description of the TLS situation."""
        if self._curl_session is not None:
            target = self._curl_target or "chrome"
            return f"chrome TLS/HTTP2 fingerprint (curl_cffi {target}, https only)"
        if self._curl_error:
            return f"standard library TLS ({self._curl_error})"
        return "standard library TLS (no impersonation backend)"

    # ------------------------------------------------------------------ fetch
    def open(
        self,
        url: str,
        *,
        method: str = "GET",
        body: Optional[bytes] = None,
        headers: Sequence[Tuple[str, str]] = (),
        timeout: Optional[float] = None,
    ) -> TransportResponse:
        """Perform one request. Redirects are *not* followed."""
        parsed = urllib.parse.urlsplit(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise TransportNetworkError(f"unsupported scheme in {url!r}")
        host = parsed.hostname or ""
        if not host:
            raise TransportNetworkError(f"no host in {url!r}")
        port = parsed.port or (443 if scheme == "https" else 80)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query

        if self._curl_session is not None and scheme == "https":
            # Only for TLS. Over cleartext, the impersonation backend would
            # try an HTTP/2 upgrade ("Upgrade: h2c"), which is something
            # Chrome never does -- it speaks HTTP/1.1 for http:// URLs. A
            # fingerprint that is wrong in the other direction is still a
            # fingerprint, so plain HTTP keeps the standard-library path.
            return self._open_with_curl(
                url, method=method, body=body, headers=headers,
                timeout=timeout or self.timeout,
            )

        deadline = timeout if timeout is not None else self.timeout
        last_error: Optional[Exception] = None
        request_timeout = max(0.5, min(float(deadline), 30.0))
        for attempt in (0, 1):
            conn = self._connection(scheme, host, port)
            selector = self._request_target(parsed, scheme, host, port, target)
            try:
                # The per-request timeout has to reach the socket: a
                # navigation deadline that only bounds the connect phase
                # would let a slow server answer long after the deadline.
                conn.timeout = request_timeout
                if conn.sock is not None:
                    conn.sock.settimeout(request_timeout)
                # `http.client` takes a mapping and sends the items in
                # insertion order, which is how the header order Chrome uses
                # survives onto the wire.
                conn.request(
                    method.upper(), selector, body=body, headers=_header_map(headers)
                )
                raw = conn.getresponse()
                payload = raw.read(MAX_BODY_BYTES + 1)
                if len(payload) > MAX_BODY_BYTES:
                    payload = payload[:MAX_BODY_BYTES]
                response = TransportResponse(
                    url=url,
                    status=raw.status,
                    reason=raw.reason or "",
                    headers=list(raw.getheaders()),
                    body=payload,
                    http_version=f"HTTP/{raw.version / 10:.1f}" if raw.version else "HTTP/1.1",
                )
                response.body = _decompress(response.body, response.content_encoding)
                self._release(conn, scheme, host, port, keep=raw.will_close is False)
                return response
            except (socket.timeout, TimeoutError) as exc:
                self._drop(scheme, host, port)
                raise TransportTimeout(f"request to {url} timed out") from exc
            except (ssl.SSLError, ConnectionError, socket.gaierror, OSError) as exc:
                last_error = exc
                self._drop(scheme, host, port)
                if attempt == 0:
                    # A reused connection can be closed by the peer between
                    # requests; one clean retry on a fresh socket is normal
                    # browser behaviour, not a way to hammer a host.
                    continue
                break
            except http.client.HTTPException as exc:
                self._drop(scheme, host, port)
                raise TransportNetworkError(f"malformed response from {url}: {exc}") from exc
        raise TransportNetworkError(f"network error for {url}: {last_error}")

    def _open_with_curl(
        self,
        url: str,
        *,
        method: str,
        body: Optional[bytes],
        headers: Sequence[Tuple[str, str]],
        timeout: float,
    ) -> TransportResponse:
        session = self._curl_session
        assert session is not None
        header_map: Dict[str, str] = {}
        for key, value in headers:
            header_map[key] = value
        extra: Dict[str, object] = {}
        if self.proxy_url:
            extra["proxy"] = self.proxy_url
        try:
            raw = session.request(
                method.upper(),
                url,
                headers=header_map,
                data=body,
                timeout=timeout,
                allow_redirects=False,
                **extra,
            )
        except Exception as exc:  # pragma: no cover - depends on the backend
            raise TransportNetworkError(f"network error for {url}: {exc}") from exc
        header_items = []
        try:
            header_items = [(k, v) for k, v in raw.headers.items()]
        except Exception:
            header_items = []
        payload = raw.content or b""
        response = TransportResponse(
            url=url,
            status=int(getattr(raw, "status_code", 0) or 0),
            reason=str(getattr(raw, "reason", "") or ""),
            headers=header_items,
            body=payload,
            http_version="HTTP/2",
        )
        # The backend decodes the body itself, so it must not be decoded a
        # second time here. The `Content-Encoding` header can still be
        # present on an already-decoded body.
        return response

    # ------------------------------------------------------------- connection
    def _request_target(
        self,
        parsed: urllib.parse.SplitResult,
        scheme: str,
        host: str,
        port: int,
        target: str,
    ) -> str:
        """The request target: a path normally, an absolute URI via proxy."""
        proxy = self._proxy_parts()
        if proxy is None:
            return target
        proxy_scheme, proxy_host, proxy_port = proxy
        if proxy_scheme == "https" and scheme == "https":
            # CONNECT tunnel: the path stays relative.
            return target
        return urllib.parse.urlunsplit((scheme, _hostport(host, port), parsed.path or "/",
                                        parsed.query, ""))

    def _proxy_parts(self) -> Optional[Tuple[str, str, int]]:
        if not self.proxy_url:
            return None
        parsed = urllib.parse.urlsplit(self.proxy_url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            return None
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 8080)
        return scheme, host, port

    def _connection(
        self, scheme: str, host: str, port: int
    ) -> http.client.HTTPConnection:
        key = (scheme, host.lower(), port)
        conn = self._pool.get(key)
        if conn is not None:
            return conn
        proxy = self._proxy_parts()
        if proxy is None:
            if scheme == "https":
                context = ssl.create_default_context()
                context.set_alpn_protocols(["http/1.1"])
                conn = http.client.HTTPSConnection(
                    host, port, timeout=self.timeout, context=context
                )
            else:
                conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
        else:
            proxy_scheme, proxy_host, proxy_port = proxy
            if scheme == "https":
                context = ssl.create_default_context()
                context.set_alpn_protocols(["http/1.1"])
                conn = http.client.HTTPSConnection(
                    proxy_host, proxy_port, timeout=self.timeout, context=context
                )
                conn.set_tunnel(host, port)
            else:
                conn = http.client.HTTPConnection(
                    proxy_host, proxy_port, timeout=self.timeout
                )
        self._pool[key] = conn
        return conn

    def _release(self, conn: http.client.HTTPConnection, scheme: str, host: str, port: int,
                 *, keep: bool) -> None:
        """Return a connection to the pool, or close it."""
        key = (scheme, host.lower(), port)
        if keep and self._pool.get(key) is conn:
            return
        self._drop(scheme, host, port)

    def _drop(self, scheme: str, host: str, port: int) -> None:
        key = (scheme, host.lower(), port)
        conn = self._pool.pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def close(self) -> None:
        """Close every pooled connection."""
        for conn in list(self._pool.values()):
            try:
                conn.close()
            except Exception:
                pass
        self._pool.clear()
        session = self._curl_session
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._curl_session = None


def _header_map(headers: Sequence[Tuple[str, str]]) -> Dict[str, str]:
    """Turn an ordered list of header pairs into an ordered mapping.

    A repeated header name keeps its first position and its last value,
    which is what a browser does for the few headers that may repeat.
    """
    out: Dict[str, str] = {}
    for name, value in headers:
        if name in out:
            out[name] = out[name] + ", " + value
        else:
            out[name] = value
    return out


def _hostport(host: str, port: int) -> str:
    if (port == 80 and ":" not in host) or (port == 443 and ":" not in host):
        return host
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _curl_impersonation_target(major: Optional[int]) -> str:
    """The ``curl_cffi`` impersonation profile matching a Chrome version.

    The TLS fingerprint carries a Chrome version of its own. If it does not
    match the version the user agent claims, the client contradicts itself
    in a way a detector can see, so the profile is chosen to match. When
    the exact version is not available, the closest older one is used: a
    slightly older Chrome is a smaller lie than a much newer one, and
    neither is as good as a match.
    """
    try:
        import typing

        from curl_cffi.requests.impersonate import (  # type: ignore
            BrowserTypeLiteral,
        )

        available = [str(item) for item in typing.get_args(BrowserTypeLiteral)]
    except Exception:
        return "chrome"
    if major is not None:
        exact = f"chrome{major}"
        if exact in available:
            return exact
    candidates: List[Tuple[int, str]] = []
    for name in available:
        if not name.startswith("chrome") or name.endswith("android"):
            continue
        digits = name[len("chrome"):]
        if not digits.isdigit():
            continue
        candidates.append((int(digits), name))
    if not candidates:
        return "chrome"
    if major is None:
        return max(candidates)[1]
    older = [item for item in candidates if item[0] <= major]
    if older:
        return max(older)[1]
    return min(candidates)[1]


def _make_curl_session(chrome_major: Optional[int] = None):
    """Build a ``curl_cffi`` session that impersonates Chrome.

    Returns ``(session, error, target)``. ``session`` is ``None`` when the
    optional backend is not installed or refused to start; ``error`` then
    says why, so the engine can report the real reason instead of "no TLS
    impersonation".
    """
    try:
        from curl_cffi import requests as curl_requests  # type: ignore
    except Exception as exc:
        return None, f"curl_cffi is not installed ({exc.__class__.__name__})", ""
    target = _curl_impersonation_target(chrome_major)
    try:
        session: Any = curl_requests.Session(impersonate=target)
    except Exception as exc:
        return None, f"curl_cffi could not start ({exc})", target
    # curl_cffi can inject its own Chrome headers; ours must win, because
    # ours is the identity the page layer reports.
    if hasattr(session, "default_headers"):
        try:
            session.default_headers = False
        except Exception:
            pass
    return session, None, target


__all__ = [
    "HttpTransport",
    "TransportError",
    "TransportNetworkError",
    "TransportResponse",
    "TransportTimeout",
    "accept_encoding_value",
    "supported_codings",
]
