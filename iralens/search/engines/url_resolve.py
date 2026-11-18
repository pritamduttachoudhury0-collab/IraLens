# -*- coding: utf-8 -*-
"""Resolve provider click-wrappers to the publisher URL they point at.

Why this module exists
----------------------
A news feed does not hand you the article; it hands you a *link to its own
redirector*. Google News returns
``news.google.com/rss/articles/CBMi…`` — an opaque, encrypted article id.

Measured on the frozen benchmark (2026-11-16, `pritamduttachoudhury0-collab/IraLens`
@ ``9a2c046``): **45.8% of all recent-news top-5 slots** were these blobs, and
reading one returned *success* with 18 characters (``"Title: Google News"``).
An agent cannot cite it, cannot fetch it, and cannot show it to a user. An
unresolvable wrapper is not a result — it is a dead end wearing a URL.

What this module does, in order
-------------------------------
1. **Offline first.** The legacy ``CBMi…`` token is a base64 protobuf with the
   publisher URL in plaintext. If it decodes, no network call happens.
2. **Then the provider's own endpoint.** Modern tokens are encrypted
   (``AU_yqLN…``), so they cannot be decoded offline by anyone — not by us, not
   by a third party. The only honest way to learn the target is to ask Google,
   using the signature the redirect page itself publishes
   (``data-n-a-id`` / ``data-n-a-ts`` / ``data-n-a-sg`` → ``batchexecute``).
3. **Then say so.** When all of that fails the result is a typed
   ``unresolved`` — never a fabricated publisher URL and never a silent pass.

Deliberately not done here: no CAPTCHA solving, no identity rotation, no
auth-token replay. This reads a signature Google serves to any anonymous
visitor of the public redirect page and asks Google to return the destination.

Caching
-------
An article id is immutable, so a successful resolution is cached for days. A
*failure* is cached briefly: the link may be a revoked or mistyped article, but
one transient network error must not permanently un-resolve an id.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ...fetch import fetch_url_text
from ...security import host_matches

# --------------------------------------------------------------------------- #
# Status vocabulary (stable; callers and tests branch on these)
# --------------------------------------------------------------------------- #

#: The URL was never a wrapper — nothing to do.
STATUS_NOT_WRAPPED = "not_wrapped"
#: Recovered offline from a legacy base64 protobuf token.
STATUS_DECODED = "decoded"
#: Recovered by asking the provider's resolution endpoint.
STATUS_RESOLVED = "resolved"
#: Recovered from the local cache.
STATUS_CACHED = "cached"
#: The provider's redirect page no longer carries a usable signature.
STATUS_NO_SIGNATURE = "no_signature"
#: The signature was accepted but the reply carried no target URL.
STATUS_NO_TARGET = "no_target"
#: Transport/HTTP failure while resolving.
STATUS_FETCH_FAILED = "fetch_failed"
#: Resolution disabled by configuration.
STATUS_DISABLED = "disabled"
#: The per-call resolution budget was exhausted.
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"

#: Statuses that mean "this wrapper now has a usable publisher URL".
RECOVERED = frozenset({STATUS_DECODED, STATUS_RESOLVED, STATUS_CACHED})
#: Statuses that mean "this wrapper must not be presented as a result".
UNRESOLVED = frozenset({STATUS_NO_SIGNATURE, STATUS_NO_TARGET,
                        STATUS_FETCH_FAILED, STATUS_DISABLED,
                        STATUS_BUDGET_EXHAUSTED})

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_BATCHEXECUTE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
#: The redirect interstitial is ~600 KB of client-rendered markup and the
#: resolution signature is emitted *last* (measured at byte 599,986 of 601,735
#: on 2026-11-16). A smaller cap silently truncates the signature and turns
#: every resolve into a false "no_signature" failure — the cap is deliberately
#: generous, and the parse is a scan of the tail, not of the whole document.
_MAX_BYTES = 4 * 1024 * 1024
#: The signature block lives at the end; scanning the last slice first keeps
#: the common case cheap even when the document grows.
_TAIL_SCAN_BYTES = 256 * 1024

_SIG_ID = re.compile(r'data-n-a-id="([^"]+)"')
_SIG_TS = re.compile(r'data-n-a-ts="(\d+)"')
_SIG_SG = re.compile(r'data-n-a-sg="([^"]+)"')
#: The endpoint answers a JSON-in-a-JSON-in-a-JSON envelope; the target URL is
#: the first http(s) string inside the ``garturlres`` payload.
_TARGET = re.compile(r'garturlres\\",\\"(https?://[^\\"]+)')

#: The resolution request body Google's own client sends. The positional array
#: is a fixed shape; only the article id, timestamp and signature vary.
#: Built from parts on purpose: this array is positional and an off-by-one
#: `null` is rejected by the endpoint with a bare `null` reply and error code 3
#: (no message). Assembly is asserted in tests so a future edit cannot silently
#: reintroduce the fault.
_GARTURLREQ_PREFIX = (
    '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
    'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
)
_GARTURLREQ = _GARTURLREQ_PREFIX + '"%s",%s,"%s"]'


@dataclass(frozen=True)
class Resolution:
    """The outcome of trying to unwrap one provider link."""

    status: str
    url: str                      # always a usable URL: the target, or the input
    detail: str = ""
    requests: int = 0             # network calls spent, for budgeting/telemetry

    @property
    def ok(self) -> bool:
        return self.status in RECOVERED

    @property
    def usable(self) -> bool:
        """True when the URL is something a caller can actually cite and read."""
        return self.status == STATUS_NOT_WRAPPED or self.ok

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "url": self.url,
                "detail": self.detail, "requests": self.requests}


# --------------------------------------------------------------------------- #
# Offline decoding
# --------------------------------------------------------------------------- #

def decode_google_news_url(url: str) -> str:
    """Return the publisher URL hidden in a *legacy* Google News token, or ``url``.

    Kept for backward compatibility: older ``CBMi…`` tokens embed the article
    URL in plaintext. Modern ``AU_yq…`` tokens are encrypted and this function
    honestly returns the input unchanged. Callers that need modern tokens must
    use :class:`GoogleNewsResolver`.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not host_matches(url, "news.google.com") or "/articles/" not in parts.path:
        return url
    seg = parts.path.rstrip("/").split("/")[-1]
    if not seg:
        return url
    import base64
    import binascii

    padded = seg + "=" * (-len(seg) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return url
    m = re.search(rb"https?://[\x20-\x7e]+", raw)
    if not m:
        return url
    candidate = m.group(0).decode("ascii", errors="ignore").rstrip("\x00")
    candidate = re.split(r"[\x00-\x1f]", candidate)[0]
    if candidate.startswith(("http://", "https://")) and "news.google.com" not in candidate:
        return candidate
    return url


def is_google_news_wrapper(url: str) -> bool:
    """True when *url* is a Google News article/redirect wrapper."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if not host_matches(url, "news.google.com"):
        return False
    return parts.path.startswith(("/rss/articles", "/articles"))


def is_bing_news_wrapper(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return host_matches(url, "bing.com") and parts.path.lower().startswith(
        ("/news/apiclick", "/apiclick"))


def decode_bing_apiclick(url: str) -> str:
    """Recover the publisher URL from a Bing News ``apiclick`` wrapper.

    The wrapper carries the destination in a plain query parameter, so this is
    fully offline.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not host_matches(url, "bing.com"):
        return url
    qs = urllib.parse.parse_qs(parts.query)
    for key in ("url", "u"):
        for value in qs.get(key, []):
            if value.startswith(("http://", "https://")) and "bing.com" not in value:
                return value
    return url


# --------------------------------------------------------------------------- #
# Network resolution
# --------------------------------------------------------------------------- #

def _http_get(url: str, *, timeout: float) -> str:
    return fetch_url_text(url, timeout=timeout, deadline_seconds=timeout,
                          max_bytes=_MAX_BYTES, user_agent=_UA,
                          same_origin_redirects=True, detect_challenge=True)


def _http_post(url: str, body: bytes, *, timeout: float) -> str:
    return fetch_url_text(url, timeout=timeout, deadline_seconds=timeout,
                          max_bytes=_MAX_BYTES, user_agent=_UA, data=body,
                          headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                          same_origin_redirects=True, detect_challenge=True)


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #

class GoogleNewsResolver:
    """Resolve Google News wrapper URLs, with caching and a per-call budget.

    ``budget`` bounds how many *network* resolutions one search may spend; a
    cache hit does not consume budget. When the budget runs out the remaining
    wrappers are reported ``budget_exhausted`` rather than silently returned.
    """

    #: An article id never changes destination, so a hit is good for days.
    SUCCESS_TTL_SECONDS = 7 * 24 * 60 * 60
    #: A failure may be transient; retry soon, but do not hammer.
    FAILURE_TTL_SECONDS = 60 * 60

    def __init__(self, *, enabled: bool = True, timeout: float = 8.0,
                 budget: int = 12, cache: Any = None,
                 transport_get: Any = None, transport_post: Any = None) -> None:
        self.enabled = enabled
        self.timeout = timeout
        self.budget = max(0, budget)
        self.cache = cache
        self._get = transport_get or _http_get
        self._post = transport_post or _http_post
        self._spent = 0
        self.stats: Dict[str, int] = {}

    # -- cache helpers -----------------------------------------------------
    def _cached(self, url: str) -> Optional[Resolution]:
        if self.cache is None:
            return None
        try:
            hit = self.cache.get("gn-resolve", {"url": url}, self.SUCCESS_TTL_SECONDS)
        except Exception:  # noqa: BLE001 - cache must never break a search
            return None
        if not hit:
            return None
        value = (hit[0] if isinstance(hit, tuple) else hit) or {}
        target = value.get("url")
        if isinstance(target, str) and target.startswith(("http://", "https://")):
            return Resolution(STATUS_CACHED, target, "", 0)
        return None

    def _remember(self, url: str, resolution: Resolution) -> None:
        if self.cache is None or not resolution.ok:
            return
        try:
            self.cache.put("gn-resolve", {"url": url},
                           {"url": resolution.url}, cacheable=True)
        except Exception:  # noqa: BLE001
            pass

    # -- public API --------------------------------------------------------
    def resolve(self, url: str) -> Resolution:
        if not is_google_news_wrapper(url):
            return Resolution(STATUS_NOT_WRAPPED, url, "", 0)

        offline = decode_google_news_url(url)
        if offline != url:
            return Resolution(STATUS_DECODED, offline, "legacy token", 0)

        cached = self._cached(url)
        if cached is not None:
            return cached

        if not self.enabled:
            return Resolution(STATUS_DISABLED, url, "resolution disabled", 0)
        if self._spent >= self.budget:
            return Resolution(STATUS_BUDGET_EXHAUSTED, url,
                              f"per-call budget of {self.budget} spent", 0)

        self._spent += 1
        sep = "&" if "?" in url else "?"
        page_url = f"{url}{sep}hl=en-US&gl=US&ceid=US:en"
        try:
            html = self._get(page_url, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            return self._finish(url, Resolution(
                STATUS_FETCH_FAILED, url, f"{type(exc).__name__}: {exc}"[:160], 1))

        aid = _SIG_ID.search(html)
        ts = _SIG_TS.search(html)
        sg = _SIG_SG.search(html)
        if not (aid and ts and sg):
            return self._finish(url, Resolution(
                STATUS_NO_SIGNATURE, url,
                "redirect page carried no resolution signature", 1))

        payload = _GARTURLREQ % (aid.group(1), int(ts.group(1)), sg.group(1))
        form = urllib.parse.urlencode(
            {"f.req": json.dumps([[["Fbv4je", payload, None, "generic"]]])}).encode()
        try:
            reply = self._post(_BATCHEXECUTE, form, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            return self._finish(url, Resolution(
                STATUS_FETCH_FAILED, url, f"{type(exc).__name__}: {exc}"[:160], 2))

        m = _TARGET.search(reply)
        if not m:
            return self._finish(url, Resolution(
                STATUS_NO_TARGET, url, "resolution reply had no target URL", 2))
        target = m.group(1).replace("\\u003d", "=").replace("\\u0026", "&")
        if not target.startswith(("http://", "https://")):
            return self._finish(url, Resolution(
                STATUS_NO_TARGET, url, "target was not an http(s) URL", 2))
        return self._finish(url, Resolution(STATUS_RESOLVED, target, "", 2))

    def _finish(self, url: str, resolution: Resolution) -> Resolution:
        self.stats[resolution.status] = self.stats.get(resolution.status, 0) + 1
        self._remember(url, resolution)
        return resolution

    def begin_search(self) -> None:
        """Start a new budget window.

        The budget is documented as *per search*, so the engine calls this at
        the start of each request. Without it a long sweep would silently stop
        resolving after the twelfth wrapper and report the rest as
        ``budget_exhausted`` for the rest of the process's life.
        """
        self._spent = 0

    def resolve_many(self, urls: list[str]) -> Dict[str, Resolution]:
        """Resolve several wrappers, preserving order and sharing the budget."""
        return {u: self.resolve(u) for u in urls}


__all__ = [
    "GoogleNewsResolver", "Resolution",
    "decode_google_news_url", "decode_bing_apiclick",
    "is_google_news_wrapper", "is_bing_news_wrapper",
    "STATUS_NOT_WRAPPED", "STATUS_DECODED", "STATUS_RESOLVED", "STATUS_CACHED",
    "STATUS_NO_SIGNATURE", "STATUS_NO_TARGET", "STATUS_FETCH_FAILED",
    "STATUS_DISABLED", "STATUS_BUDGET_EXHAUSTED", "RECOVERED", "UNRESOLVED",
]
