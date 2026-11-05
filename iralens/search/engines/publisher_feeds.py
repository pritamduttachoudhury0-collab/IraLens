# -*- coding: utf-8 -*-
"""Curated publisher-feed discovery — the local, regional and beat lane.

The news *search* feeds (Bing/Google News) index what their crawlers chose to
index, which skews global and English-language. A regional story — an Assam
flood, a Northeast district decision — is often published by a local outlet
that those indexes surface late or never. Reading the publisher's own feed
directly reaches it.

This lane selects a bounded set of feeds from the curated source pack using the
question's implied region and beats, fetches them over the shared SSRF-safe
transport with per-host pacing, and keeps the items that address the query
inside the requested window. Every item carries the publisher's own
``pubDate`` as a **publication** date, to be corroborated against the article
page by the orchestrator's verification step.

It is an additional lane, never a replacement: the general engines and news
search feeds still run, so a publisher being down costs coverage, not the query.
"""
from __future__ import annotations

import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional

from ...errors import IraLensError, SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.schema import SearchFilters, SearchHit
from ..querytext import term_set
from ..sources_pack import PublisherFeed, select_feeds
from .base import NATIVE, POST, UNSUPPORTED, SearchBackend
from .news_rss import parse_news_rss

_USER_AGENT = "IraLens/0.3.5 (https://github.com/pritamduttachoudhury0-collab/IraLens)"
_MAX_BYTES = 3 * 1024 * 1024
#: How many publisher feeds one query may read. Bounded so a news question
#: cannot fan out into dozens of publisher requests.
MAX_FEEDS_PER_QUERY = 4


def filter_feed_hits(hits: List[SearchHit], query: str, *,
                     date_from: Optional[str] = None,
                     date_to: Optional[str] = None,
                     min_term_overlap: int = 1) -> List[SearchHit]:
    """Keep feed items that address the query and fall inside the window.

    A feed is a firehose of the publisher's latest stories, not a search
    result, so the query match happens here. Matching is on title + summary
    tokens; an item with no overlap is not evidence for this question.
    """
    q_terms = term_set(query)
    kept: List[SearchHit] = []
    for hit in hits:
        haystack = term_set(f"{hit.title} {hit.snippet}")
        if q_terms and len(q_terms & haystack) < max(1, min_term_overlap):
            continue
        published = hit.published_at
        if published:
            if date_from and published < date_from:
                continue
            if date_to and published > date_to:
                continue
        kept.append(hit)
    for position, hit in enumerate(kept, start=1):
        hit.position = position
    return kept


class PublisherFeedsBackend(SearchBackend):
    """Reads curated publisher RSS/Atom feeds directly (keyless, bounded)."""

    name = "publisher-feeds"
    paginated = False
    handled = {"date_from": POST, "date_to": POST, "include_domains": NATIVE,
               "exclude_domains": POST, "file_type": UNSUPPORTED,
               "language": NATIVE, "region": NATIVE}

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        if offset:
            raise SourceUnavailableError("publisher-feeds does not support verified pagination",
                                         detail="pagination_unsupported")
        settings = getattr(context, "config", None)
        try:
            configured = max(1, int(settings.get("search_timeout_seconds", 10))) if settings else 10
        except (TypeError, ValueError):
            configured = 10
        timeout = min(configured, 10)
        feeds = select_feeds(query, region=filters.region, language=filters.language,
                             limit=MAX_FEEDS_PER_QUERY, domains=filters.include_domains)
        if not feeds:
            raise SourceUnavailableError("no curated publisher feed matches this query",
                                         detail="no_matching_feed")

        # Different publishers are different hosts, so reading a few feeds in
        # parallel does not violate any single host's pacing.
        workers = min(len(feeds), MAX_FEEDS_PER_QUERY)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(
                lambda feed: self._read_feed(feed, query, filters, limit, timeout), feeds))

        hits: List[SearchHit] = []
        failures: List[str] = []
        # pool.map preserves order and length exactly; a mismatch would be a
        # bug, so let it surface rather than silently pairing wrong feeds.
        for feed, outcome in zip(feeds, results, strict=True):
            if isinstance(outcome, Exception):
                failures.append(f"{feed.domain}: {type(outcome).__name__}")
                continue
            hits.extend(outcome)
        if not hits and len(failures) == len(feeds):
            raise SourceUnavailableError("curated publisher feeds could not be read",
                                         detail="; ".join(failures)[:300])
        # Rank within the lane by recency then feed order; the orchestrator
        # re-ranks everything together afterwards.
        hits.sort(key=lambda h: (h.published_at or "", -h.position), reverse=True)
        for position, hit in enumerate(hits[:max(1, limit)], start=1):
            hit.position = position
        return hits[:max(1, limit)]

    def _read_feed(self, feed: PublisherFeed, query: str, filters: SearchFilters,
                   limit: int, timeout: int):
        try:
            host = urllib.parse.urlsplit(feed.url).hostname or feed.domain
            HOST_RATE_LIMITER.set_interval(host, 1.0)
            HOST_RATE_LIMITER.wait(host)
            xml = fetch_url_text(
                feed.url, timeout=timeout, max_bytes=_MAX_BYTES,
                headers={"Accept": "application/rss+xml, application/xml, text/xml, */*",
                         "User-Agent": _USER_AGENT},
                detect_challenge=True,
            )
            if not xml or not xml.strip():
                raise SourceUnavailableError(f"{feed.name} returned an empty feed",
                                             detail="empty_feed")
            # A generous parse limit: the query filter runs afterwards, and a
            # busy publisher's relevant story may not be in the first 5 items.
            parsed = parse_news_rss(xml, query, 60, engine=self.name)
            for hit in parsed:
                hit.raw["publisher"] = feed.name
                hit.raw["source_tier"] = feed.tier
                hit.raw["feed_url"] = feed.url
                hit.raw["discovered_via"] = "publisher_feed"
            return filter_feed_hits(parsed, query, date_from=filters.date_from,
                                    date_to=filters.date_to)
        except IraLensError as exc:
            return exc
        except Exception as exc:  # one publisher never aborts the lane
            return exc


__all__ = ["PublisherFeedsBackend", "filter_feed_hits", "MAX_FEEDS_PER_QUERY"]
