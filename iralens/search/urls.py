# -*- coding: utf-8 -*-
"""URL canonicalization for deduplication and provenance.

Two URLs are the same *page* for dedup when their canonical forms match. The
rules are conservative: we drop only tracking parameters and fragments and
normalize scheme, host case, `www.`, and trailing slashes. Path case and
other query parameters are kept, because they can change the page.
"""

from __future__ import annotations

import urllib.parse
from typing import Optional

TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "yclid", "igshid",
    "mc_cid", "mc_eid", "ref_src", "ref", "spm", "_ga", "_hsenc", "_hsmi",
})


def is_tracking_param(name: str) -> bool:
    low = name.lower()
    return low.startswith("utm_") or low in TRACKING_PARAMS


def canonical_url(url: str) -> Optional[str]:
    """Return the dedup key for an http(s) URL, or None if it is not web-like."""
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = [
        (k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not is_tracking_param(k)
    ]
    query.sort()
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urllib.parse.urlunsplit(("https", netloc, path, urllib.parse.urlencode(query), ""))


def registrable_host(url: str) -> str:
    """Last two labels of the host ('news.bbc.co.uk' -> 'co.uk'-aware check is not
    attempted; this is used only to group near-duplicates conservatively)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def domain_of(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def domain_matches(host: str, domain: str) -> bool:
    """True if host equals domain or is a subdomain of it."""
    return host == domain or host.endswith("." + domain)
