# -*- coding: utf-8 -*-
"""Public-HTTP(S) URL guard for PyNewsBrowser — a fix for an SSRF hole.

Why this file exists
--------------------
PyNewsBrowser's ``HttpClient`` opens ``httpx.AsyncClient(follow_redirects=True)``
and never inspects the URL it is asked to fetch. Two of its entry points take a
URL straight from the caller:

* ``GET /api/read?url=...`` in ``webapp.py``
* ``ops learn-json --url-template ...`` in ``newsops/learn.py``

Combined with the documented ``serve --host 0.0.0.0``, that turns the app into
an open fetch proxy. A visitor can make the *server* request
``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` (cloud
instance credentials), ``http://127.0.0.1:6379/`` (a local Redis), or any host
on the server's private network, and read the response body back.

Following redirects makes it worse: even a caller-supplied *public* URL can
answer ``302 -> http://169.254.169.254/...``, so validating only the first URL
is not enough. Every hop has to be re-checked.

What this does
--------------
``normalize_public_http_url`` rejects anything that is not a clean public
HTTP(S) target: non-HTTP schemes, embedded credentials, loopback, link-local
(including the cloud metadata address), RFC1918 and other non-global IP
literals, single-label hosts, and ``.local`` / ``.internal`` style names.
``SafeRedirects`` re-validates each redirect hop.

It does **not** try to defeat CAPTCHAs, bot checks, or login walls. Those are
reported, not bypassed.

Provenance
----------
Ported from IraLens (``iralens/security.py``), which already enforced this
policy on its own fetch path. Self-contained: standard library only, so it
drops into PyNewsBrowser without adding a dependency.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlsplit

#: Hosts that are never a legitimate public target, whatever else is configured.
NEVER_ALLOWED_HOSTS = frozenset({"instance-data", "metadata.google.internal", "metadata"})

_BLOCKED_HOSTS = frozenset({
    "home.arpa", "instance-data", "internal", "ip6-localhost", "ip6-loopback",
    "lan", "local", "localdomain", "localhost", "metadata.google.internal",
})

_BLOCKED_SUFFIXES = (
    ".home.arpa", ".internal", ".lan", ".local", ".localdomain", ".localhost",
)

#: The cloud metadata address. Blocked by the link-local rule too; named here
#: so the reason is obvious to anyone reading a denial.
METADATA_ADDRESS = "169.254.169.254"


class UnsafeURLError(ValueError):
    """The URL is not a clean public HTTP(S) target."""


def _literal_ip(host: str) -> Optional[ipaddress.IPv4Address]:
    """Parse an IP literal, including the legacy short forms.

    ``socket.inet_aton`` accepts ``127.1``, ``2130706433`` and ``0177.0.0.1``,
    all of which are loopback. A guard that only understands dotted quads is
    trivially walked around.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def normalize_public_http_url(url: str) -> str:
    """Return the normalized URL, or raise :class:`UnsafeURLError`.

    Text-level validation only. It cannot see where a hostname *resolves*, so
    it is paired with per-hop redirect validation and (optionally) DNS pinning
    at the transport layer.
    """
    candidate = str(url or "").strip()
    if (not candidate or "\\" in candidate
            or any(c.isspace() or ord(c) < 0x20 or c == "\x7f" for c in candidate)):
        raise UnsafeURLError("only public HTTP(S) URLs are allowed")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port  # rejects malformed / out-of-range authorities
    except (TypeError, ValueError):
        raise UnsafeURLError("only public HTTP(S) URLs are allowed") from None

    literal = _literal_ip(host)
    if (parsed.scheme.lower() not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or "%" in host                      # zone id / percent-encoded host
            or host in NEVER_ALLOWED_HOSTS
            or host in _BLOCKED_HOSTS
            or host.endswith(_BLOCKED_SUFFIXES)
            or ("." not in host and literal is None)
            or (literal is not None and not literal.is_global)):
        raise UnsafeURLError("only public HTTP(S) URLs are allowed")

    return parsed.geturl()


def is_public_http_url(url: str) -> bool:
    """Non-raising form, for filtering a list of candidate links."""
    try:
        normalize_public_http_url(url)
    except UnsafeURLError:
        return False
    return True


def resolve_is_public(host: str) -> bool:
    """Whether *every* address a hostname resolves to is a public one.

    A name that resolves to both a public and a private address is a DNS
    rebinding shape and is refused. Call this before connecting when the URL
    came from an untrusted caller.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    for raw in addresses:
        try:
            if not ipaddress.ip_address(raw.split("%")[0]).is_global:
                return False
        except ValueError:
            return False
    return True


class SafeRedirects:
    """Manual redirect following that re-validates every hop.

    Use instead of ``follow_redirects=True``::

        client = httpx.AsyncClient(follow_redirects=False)
        resp = await SafeRedirects(client).get(url)
    """

    def __init__(self, client, *, max_hops: int = 5, check_dns: bool = False):
        self.client = client
        self.max_hops = max_hops
        self.check_dns = check_dns

    async def get(self, url: str, **kwargs):
        current = normalize_public_http_url(url)
        for _ in range(self.max_hops + 1):
            if self.check_dns:
                host = urlsplit(current).hostname or ""
                if not resolve_is_public(host):
                    raise UnsafeURLError("host does not resolve to a public address")
            response = await self.client.get(current, follow_redirects=False, **kwargs)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location")
            if not location:
                return response
            # A relative Location is resolved against the current URL, then
            # re-validated — this is the hop that defeats a naive guard.
            from urllib.parse import urljoin
            current = normalize_public_http_url(urljoin(current, location))
        raise UnsafeURLError("too many redirects")


__all__ = [
    "METADATA_ADDRESS",
    "NEVER_ALLOWED_HOSTS",
    "SafeRedirects",
    "UnsafeURLError",
    "is_public_http_url",
    "normalize_public_http_url",
    "resolve_is_public",
]
