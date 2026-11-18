# -*- coding: utf-8 -*-
"""Keyless news-discovery backends: Bing News RSS and Google News RSS.

These fill the gap that made recent-news retrieval score 0/25: GDELT returns
direct publisher URLs but *no* publication date (its ``seendate`` is an
index-seen time), and the general web engines return undated SERP hits. A news
RSS feed carries the publisher's own ``pubDate`` and a link to the article, so
a news query can finally return a result with a *verifiable publication date*
and a *direct publisher URL*.

Both endpoints are public, keyless RSS. They are used as *discovery* sources:
the orchestrator still reads the actual publisher page to verify the date and
the topic before a result is presented as fresh evidence. The feed's
``pubDate`` is the publisher's stated publication time (``date_kind =
"publication"``); it is corroborated against the page metadata when read.

Parsing is a pure function of the feed XML (fixture-testable offline). The
network step is the shared SSRF-safe ``fetch_url_text``. Provider terms and
rate limits are respected via the process-wide host pacer; a failed feed is a
typed ``SourceUnavailableError``, never a silent zero-result success.
"""
from __future__ import annotations

import math
import urllib.parse
from typing import Any, Callable, List, Optional
from xml.etree import ElementTree as ET

from ...errors import SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.schema import SearchFilters, SearchHit
from .base import POST, UNSUPPORTED, SearchBackend
from .url_resolve import (GoogleNewsResolver, decode_bing_apiclick,
                          decode_google_news_url, is_google_news_wrapper)

_USER_AGENT = "IraLens/0.3.5 (https://github.com/pritamduttachoudhury0-collab/IraLens)"
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024

BING_NEWS_ENDPOINT = "https://www.bing.com/news/search"
GOOGLE_NEWS_ENDPOINT = "https://news.google.com/rss/search"


# --------------------------------------------------------------------------- #
# URL unwrapping
# --------------------------------------------------------------------------- #

#: ``decode_bing_apiclick`` and ``decode_google_news_url`` live in
#: :mod:`~iralens.search.engines.url_resolve` and are re-exported here so that
#: existing callers and tests keep their import path. They are pure functions:
#: legacy tokens decode offline, modern encrypted Google tokens do not, and an
#: undecodable token is returned unchanged rather than guessed at.
def unwrap_feed_link(url: str, *, resolver_factory=None) -> tuple[str, str]:
    """Unwrap a provider link, returning ``(url, status)``.

    This is the seam the RSS backend uses. ``status`` is the resolver's typed
    vocabulary; ``unresolved`` means the link is still a wrapper and must not
    be presented as a result. Kept separate from the parsers so the parsing
    path stays a pure function of the XML.
    """
    if not is_google_news_wrapper(url):
        return decode_bing_apiclick(url), "not_wrapped"
    factory = resolver_factory or _default_resolver
    resolution = factory().resolve(url)
    return resolution.url, resolution.status


_RESOLVER: Optional[GoogleNewsResolver] = None


def _default_resolver(settings: Any = None) -> GoogleNewsResolver:
    """The process-wide resolver, sharing the engine's response cache.

    One instance keeps the per-search budget and hit-rate statistics
    meaningful across the queries of a single sweep.
    """
    global _RESOLVER
    if _RESOLVER is None:
        from ...cache import ResponseCache
        from ...config import state_dir
        _RESOLVER = GoogleNewsResolver(
            enabled=_flag(settings, "news_resolve_wrappers", True),
            timeout=_num(settings, "news_resolve_timeout_seconds", 8.0),
            budget=int(_num(settings, "news_resolve_budget", 12)),
            cache=ResponseCache(enabled=_flag(settings, "cache_enabled", True),
                                directory=state_dir() / "cache"),
        )
    return _RESOLVER


def _raw(settings: Any, key: str, default: Any) -> Any:
    try:
        return settings.get(key, default) if settings is not None else default
    except AttributeError:
        return default


def _flag(settings: Any, key: str, default: bool) -> bool:
    """Coerce a config value to a bool.

    ``Config.get`` returns whatever the environment holds, so ``"0"``, ``"off"``
    and ``"false"`` all arrive as strings — and every non-empty string is
    truthy. Passing one straight through silently ignored the setting.
    """
    value = _raw(settings, key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _num(settings: Any, key: str, default: float) -> float:
    value = _raw(settings, key, default)
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) else default
    except (TypeError, ValueError):
        return float(default)


def reset_resolver() -> None:
    """Drop the cached resolver (used by tests and by a settings change)."""
    global _RESOLVER
    _RESOLVER = None


# --------------------------------------------------------------------------- #
# Pure RSS parsing
# --------------------------------------------------------------------------- #

def _text(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def _parse_rss_items(xml: str) -> List[ET.Element]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SourceUnavailableError("news feed returned malformed XML",
                                     detail="invalid_xml") from exc
    # RSS 2.0: channel/item; Atom: entry. Handle both.
    items = root.findall(".//item")
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//a:entry", ns) or root.findall(".//entry")
    return items


def _item_link(item: ET.Element) -> str:
    link = _text(item.find("link"))
    if link:
        return link
    # Atom <link href="..."/>
    for ln in item.findall("link"):
        href = ln.get("href")
        if href:
            return href
    guid = _text(item.find("guid"))
    if guid.startswith(("http://", "https://")):
        return guid
    return ""


def _item_pubdate(item: ET.Element) -> str:
    for tag in ("pubDate", "published", "updated", "date"):
        val = _text(item.find(tag))
        if val:
            return val
    # Dublin Core dc:date
    for child in item:
        if child.tag.endswith("}date") or child.tag == "date":
            val = _text(child)
            if val:
                return val
    return ""


def _item_source(item: ET.Element) -> str:
    src = item.find("source")
    if src is not None:
        return _text(src)
    # Bing News uses <News:Source>
    for child in item:
        if child.tag.lower().endswith("source"):
            return _text(child)
    return ""


def parse_news_rss(xml: str, query: str, limit: int, *, engine: str,
                   unwrap=None, resolution_log: Optional[dict] = None) -> List[SearchHit]:
    """Parse a news RSS/Atom feed into normalized hits with publication dates.

    ``unwrap`` decodes a provider click-wrapper to the direct publisher URL.
    It may either return a URL (the historical contract) or a ``(url, status)``
    pair; the status is accumulated into ``resolution_log`` so a caller can tell
    "the feed was all wrappers we could not resolve" from "the feed was empty".

    A hit whose wrapper could not be resolved is still parsed — with its
    ``raw["wrapper_unresolved"] = True`` marker set — because the *orchestrator*
    owns the decision to drop it. Dropping it silently here would turn a
    resolution outage into a plausible-looking empty feed.

    The feed ``pubDate`` becomes ``published_at`` with ``date_kind =
    "publication"`` and ``date_evidence = "rss:pubDate"`` — the publisher's
    stated time, to be corroborated against the page when read.
    """
    from ..news import normalize_timestamp

    items = _parse_rss_items(xml)
    hits: List[SearchHit] = []
    for item in items:
        if len(hits) >= max(0, limit):
            break
        title = _text(item.find("title"))
        link = _item_link(item)
        if not title or not link:
            continue
        wrapper_status = ""
        if unwrap is not None:
            outcome = unwrap(link)
            if isinstance(outcome, tuple):
                link, wrapper_status = outcome[0], outcome[1]
            else:
                link = outcome
        try:
            parts = urllib.parse.urlsplit(link)
        except ValueError:
            continue
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            continue
        snippet = _text(item.find("description"))
        if not snippet:
            snippet = _text(item.find("summary"))
        pub_raw = _item_pubdate(item)
        published = normalize_timestamp(pub_raw)
        source = _item_source(item)
        raw: dict[str, Any] = {"provider": engine, "feed_pubdate": pub_raw}
        if source:
            raw["publisher"] = source
        if wrapper_status and wrapper_status not in ("not_wrapped", "decoded",
                                                     "resolved", "cached"):
            # The feed gave us a middleman we could not see through. Marked so
            # the orchestrator drops it and the diagnostics can say why.
            raw["wrapper_unresolved"] = True
            raw["wrapper_status"] = wrapper_status
            if resolution_log is not None:
                resolution_log[wrapper_status] = resolution_log.get(wrapper_status, 0) + 1
        elif resolution_log is not None and wrapper_status:
            resolution_log[wrapper_status] = resolution_log.get(wrapper_status, 0) + 1
        hits.append(SearchHit(
            title=title[:240], url=link, snippet=snippet[:400], engine=engine,
            position=len(hits) + 1, published_at=published, query=query, raw=raw,
            date_kind="publication" if published else "",
        ))
        if published:
            hits[-1].raw["date_evidence"] = "rss:pubDate"
    return hits


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

class _NewsRssBackend(SearchBackend):
    """Shared machinery for a keyless news RSS endpoint."""

    endpoint = ""
    host = ""
    extra_params: dict = {}
    unwrap: Callable[[str], str] = staticmethod(lambda u: u)

    handled = {"date_from": POST, "date_to": POST, "include_domains": POST,
               "exclude_domains": POST, "file_type": UNSUPPORTED,
               "language": POST, "region": POST}
    paginated = False

    def _params(self, query: str, filters: SearchFilters, limit: int) -> dict:
        raise NotImplementedError

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        if offset:
            raise SourceUnavailableError(f"{self.name} does not support verified pagination",
                                         detail="pagination_unsupported")
        settings = getattr(context, "config", None)
        # Exposed to ``unwrap`` so the resolver honours operator settings
        # (enabled / timeout / budget) without threading them through every
        # pure parser signature.
        self._settings = settings
        if self.name == "google-news":
            # A fresh budget window per search, not per process. Compared by
            # name, not isinstance: this method runs from the shared base
            # class, where the subclass is not in scope.
            _default_resolver(settings).begin_search()
        try:
            configured = max(1, int(settings.get("search_timeout_seconds", 10))) if settings else 10
        except (TypeError, ValueError):
            configured = 10
        timeout = min(configured, 12)
        params = self._params(query, filters, limit)
        url = self.endpoint + "?" + urllib.parse.urlencode(params)
        HOST_RATE_LIMITER.set_interval(self.host, 1.0)
        HOST_RATE_LIMITER.wait(self.host)
        try:
            xml = fetch_url_text(
                url, timeout=timeout, max_bytes=_MAX_RESPONSE_BYTES,
                headers={"Accept": "application/rss+xml, application/xml, text/xml, */*",
                         "User-Agent": _USER_AGENT},
                detect_challenge=True,
            )
        except SourceUnavailableError:
            raise
        except Exception as exc:
            # A typed failure, never a silent empty success.
            from ...errors import IraLensError
            if isinstance(exc, IraLensError):
                raise
            raise SourceUnavailableError(f"{self.name} request failed",
                                         detail=type(exc).__name__) from exc
        if not xml or not xml.strip():
            raise SourceUnavailableError(f"{self.name} returned an empty feed",
                                         detail="empty_feed")
        resolution_log: dict = {}
        # Over-fetch. A feed that is half portals would otherwise hand the
        # orchestrator five candidates of which two or three get dropped for
        # being unreadable, leaving the response short. Asking for more than
        # we intend to show costs one slightly larger RSS body.
        wants = max(1, limit)
        fetch_limit = int(round(wants * max(1.0, _num(settings, "news_feed_overfetch", 2.0))))
        hits = parse_news_rss(xml, query, fetch_limit, engine=self.name,
                              unwrap=self.unwrap, resolution_log=resolution_log)
        if resolution_log:
            # Diagnostics travel with the hits: a caller can tell how many
            # feed links were wrappers and how many of those are still opaque,
            # instead of inferring it from a short result list.
            self.last_resolution_log = resolution_log
        return hits


class BingNewsBackend(_NewsRssBackend):
    """Bing News RSS — keyless, returns publisher pubDate + direct article URL."""

    name = "bing-news"
    endpoint = BING_NEWS_ENDPOINT
    host = "www.bing.com"
    unwrap = staticmethod(decode_bing_apiclick)

    def _params(self, query: str, filters: SearchFilters, limit: int) -> dict:
        # Ask Bing for enough rows that a portal-heavy page still yields a
        # full publisher list. Measured 2026-11-16: ~47% of Bing News links are
        # msn/yahoo portals, so a 2x ask is the floor, not padding.
        params = {"q": query, "format": "rss",
                  "count": str(min(50, max(10, int(round(limit * 3)))))}
        if filters.language:
            params["setlang"] = filters.language
        if filters.region:
            params["mkt"] = filters.region
        return params


class GoogleNewsBackend(_NewsRssBackend):
    """Google News RSS — keyless, returns publisher pubDate + article link."""

    name = "google-news"
    endpoint = GOOGLE_NEWS_ENDPOINT
    host = "news.google.com"

    #: Only legacy tokens decode offline; modern ones need the provider's own
    #: resolution endpoint. The resolver reports a typed status either way so
    #: an opaque link is visible instead of being passed off as a result.
    def unwrap(self, url: str):
        if not is_google_news_wrapper(url):
            return decode_google_news_url(url)
        legacy = decode_google_news_url(url)
        if legacy != url:
            return legacy, "decoded"
        settings = getattr(self, "_settings", None)
        resolution = _default_resolver(settings).resolve(url)
        return resolution.url, resolution.status

    def _params(self, query: str, filters: SearchFilters, limit: int) -> dict:
        q = query
        # Google News ranks by relevance by default, which for a low-volume
        # topic surfaces years-old articles first: measured for "earthquake
        # today magnitude USGS", 28 of 30 rows were from 2016-2025 and zero
        # from today. Its `when:` operator is the only server-side recency
        # control the RSS endpoint has, so a caller-supplied window must be
        # used even when it is a single day (`days == 0`).
        when = ""
        if filters.date_from:
            from datetime import date as _date
            try:
                days = (_date.today() - _date.fromisoformat(filters.date_from)).days
                if 0 <= days <= 365:
                    when = f"when:{max(1, days)}d"
            except ValueError:
                when = ""
        elif filters.date_to:
            # A caller-supplied upper bound with no lower bound still means
            # "recent", and the archive lane covers the old end.
            when = "when:7d"
        if when:
            q = f"{query} {when}"
        params = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        if filters.language:
            params["hl"] = filters.language
        return params


__all__ = [
    "BingNewsBackend", "GoogleNewsBackend", "parse_news_rss",
    "decode_bing_apiclick", "decode_google_news_url",
]
