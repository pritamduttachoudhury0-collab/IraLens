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

import base64
import binascii
import re
import urllib.parse
from typing import Any, Callable, List, Optional
from xml.etree import ElementTree as ET

from ...errors import SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.schema import SearchFilters, SearchHit
from .base import POST, UNSUPPORTED, SearchBackend

_USER_AGENT = "IraLens/0.3.5 (https://github.com/pritamduttachoudhury0-collab/IraLens)"
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024

BING_NEWS_ENDPOINT = "https://www.bing.com/news/search"
GOOGLE_NEWS_ENDPOINT = "https://news.google.com/rss/search"


# --------------------------------------------------------------------------- #
# URL unwrapping
# --------------------------------------------------------------------------- #

def decode_bing_apiclick(url: str) -> str:
    """Bing News wraps links as ``.../apiclick.aspx?...&url=<encoded>``.

    The ``url`` parameter is the direct publisher URL. Return it when present;
    otherwise return the input unchanged.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc.endswith("bing.com"):
        return url
    qs = urllib.parse.parse_qs(parts.query)
    for key in ("url", "u"):
        for value in qs.get(key, []):
            if value.startswith(("http://", "https://")):
                return value
    return url


def decode_google_news_url(url: str) -> str:
    """Best-effort decode of a Google News ``/rss/articles/<base64>`` link.

    The legacy ``CBMi…`` form is a base64url protobuf that embeds the article
    URL in plaintext; we extract it. Newer encrypted forms cannot be decoded
    offline, so the Google News URL is returned unchanged (it is still a valid,
    readable redirect — just not the publisher canonical). We never fabricate
    a publisher URL we cannot recover.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc.endswith("news.google.com"):
        return url
    seg = parts.path.rstrip("/").split("/")[-1]
    if not seg:
        return url
    padded = seg + "=" * (-len(seg) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return url
    # The protobuf carries the URL as a length-prefixed http(s) string.
    m = re.search(rb"https?://[\x20-\x7e]+", raw)
    if not m:
        return url
    candidate = m.group(0).decode("ascii", errors="ignore").rstrip("\x00")
    # Trim trailing protobuf field bytes that are not part of the URL.
    candidate = re.split(r"[\x00-\x1f]", candidate)[0]
    if candidate.startswith(("http://", "https://")):
        return candidate
    return url


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
                   unwrap=None) -> List[SearchHit]:
    """Parse a news RSS/Atom feed into normalized hits with publication dates.

    ``unwrap`` decodes a provider click-wrapper to the direct publisher URL.
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
        if unwrap is not None:
            link = unwrap(link)
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
        raw = {"provider": engine, "feed_pubdate": pub_raw}
        if source:
            raw["publisher"] = source
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
        return parse_news_rss(xml, query, max(1, limit), engine=self.name, unwrap=self.unwrap)


class BingNewsBackend(_NewsRssBackend):
    """Bing News RSS — keyless, returns publisher pubDate + direct article URL."""

    name = "bing-news"
    endpoint = BING_NEWS_ENDPOINT
    host = "www.bing.com"
    unwrap = staticmethod(decode_bing_apiclick)

    def _params(self, query: str, filters: SearchFilters, limit: int) -> dict:
        params = {"q": query, "format": "rss", "count": str(min(50, max(10, limit * 2)))}
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
    unwrap = staticmethod(decode_google_news_url)

    def _params(self, query: str, filters: SearchFilters, limit: int) -> dict:
        q = query
        # Google News supports a when:<span> operator for recency.
        if filters.date_from:
            from datetime import date as _date
            try:
                days = (_date.today() - _date.fromisoformat(filters.date_from)).days
                if 0 < days <= 365:
                    q = f"{query} when:{max(1, days)}d"
            except ValueError:
                pass
        params = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        if filters.language:
            params["hl"] = filters.language
        return params


__all__ = [
    "BingNewsBackend", "GoogleNewsBackend", "parse_news_rss",
    "decode_bing_apiclick", "decode_google_news_url",
]
