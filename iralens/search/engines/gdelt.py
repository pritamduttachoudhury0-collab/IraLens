# -*- coding: utf-8 -*-
"""GDELT DOC 2.0 public news search API (keyless, intent-routed).

GDELT indexes reporting from a broad set of international news sources and
returns direct publisher URLs. It supplements, rather than replaces, general
web search and is called only for current-event/news-shaped queries. The
`seendate` field is retained as provider provenance; it is an index-seen time,
not a publication date, so it is never used as `published_at`.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from ...errors import IraLensError, SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.schema import SearchFilters, SearchHit
from .base import NATIVE, POST, SearchBackend

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
_HOST = "api.gdeltproject.org"
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024
_USER_AGENT = "IraLens/0.3.1 (https://github.com/pritamduttachoudhury0-collab/IraLens)"


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _seen_date(value: Any) -> Optional[str]:
    """Normalize GDELT's index-seen timestamp without calling it publication."""
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})(\d{2})(\d{2})T", raw)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def parse_gdelt_payload(payload: Any, query: str, limit: int) -> List[SearchHit]:
    """Parse a GDELT ArtList response into normalized hits, without I/O."""
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return []
    hits: List[SearchHit] = []
    for article in articles:
        if len(hits) >= max(0, limit):
            break
        if not isinstance(article, dict):
            continue
        title = _clean(article.get("title"), 240)
        url = _clean(article.get("url"), 2048)
        try:
            parts = urlsplit(url)
            hostname = parts.hostname
        except ValueError:
            continue
        if not title or parts.scheme.lower() not in ("http", "https") or not hostname:
            continue
        domain = _clean(article.get("domain") or parts.hostname, 255).lower()
        language = _clean(article.get("language"), 32)
        source_country = _clean(article.get("sourceCountry"), 32)
        raw: Dict[str, Any] = {"provider": "gdelt", "domain": domain}
        seen = _seen_date(article.get("seendate"))
        if seen:
            raw["seen_at"] = seen
        if language:
            raw["language"] = language
        if source_country:
            raw["source_country"] = source_country
        # GDELT ArtList does not provide a reliable article publication time.
        # Do not turn its index timestamp into fabricated freshness evidence.
        hits.append(SearchHit(
            title=title, url=url, snippet="", engine="gdelt",
            position=len(hits) + 1, published_at=None, query=query, raw=raw,
        ))
    return hits


class GdeltBackend(SearchBackend):
    """Keyless broad news index for queries that ask about current events."""

    name = "gdelt"
    # The public DOC API's pagination contract is not used until it is verified;
    # refusing nonzero offsets is safer than repeating page one as new results.
    paginated = False
    handled = {"date_from": NATIVE, "date_to": NATIVE, "include_domains": POST,
               "exclude_domains": POST, "file_type": POST,
               "language": POST, "region": POST}

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        if offset:
            raise SourceUnavailableError("GDELT does not support verified pagination",
                                         detail="pagination_unsupported")
        settings = getattr(context, "config", None)
        try:
            configured_timeout = max(1, int(settings.get("search_timeout_seconds", 8))) if settings else 8
        except (TypeError, ValueError):
            configured_timeout = 8
        # Keep an unavailable public endpoint from dominating the entire search
        # latency budget; a deployment can still choose a shorter timeout.
        timeout = min(configured_timeout, 8)
        max_records = min(100, max(20, max(1, int(limit)) * 6))
        params: Dict[str, str] = {
            "query": query.strip(),
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max_records),
            "sort": "HybridRel",
        }
        if filters.date_from:
            params["startdatetime"] = filters.date_from.replace("-", "") + "000000"
        if filters.date_to:
            params["enddatetime"] = filters.date_to.replace("-", "") + "235959"
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        HOST_RATE_LIMITER.set_interval(_HOST, 1.0)
        HOST_RATE_LIMITER.wait(_HOST)
        try:
            text = fetch_url_text(
                url, timeout=timeout, max_bytes=_MAX_RESPONSE_BYTES,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
            payload = json.loads(text)
        except IraLensError:
            raise
        except ValueError as exc:
            raise SourceUnavailableError("GDELT returned an invalid response",
                                         detail="invalid_json") from exc
        except Exception as exc:
            raise SourceUnavailableError("GDELT news search request failed",
                                         detail=type(exc).__name__) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
            raise SourceUnavailableError("GDELT returned an invalid response",
                                         detail="invalid_json_shape")
        return parse_gdelt_payload(payload, query, max_records)
