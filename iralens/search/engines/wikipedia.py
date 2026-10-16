# -*- coding: utf-8 -*-
"""Wikipedia's official MediaWiki search API (free, no API key).

This is a focused supplemental source, not a general web-search index. Calls
use the public API with an identifying User-Agent and a one-request-per-second
process-wide pace, in line with Wikimedia's API etiquette. The result URL and
snippet make the source attribution explicit.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.parse
from typing import Any, List

from ...errors import SourceUnavailableError
from ...fetch import fetch_url_text
from ...search.schema import SearchFilters, SearchHit
from .base import POST, UNSUPPORTED, SearchBackend

ENDPOINT = "https://en.wikipedia.org/w/api.php"
_USER_AGENT = "IraLens/0.1 (https://github.com/pritamduttachoudhury0-collab/IraLens)"
_RATE_LOCK = threading.Lock()
_LAST_CALL = 0.0


def parse_wikipedia_payload(payload: Any, query: str, limit: int) -> List[SearchHit]:
    """Pure parser for the MediaWiki `list=search` response."""
    if not isinstance(payload, dict):
        return []
    query_data = payload.get("query")
    search = query_data.get("search") if isinstance(query_data, dict) else None
    if not isinstance(search, list):
        return []
    hits: List[SearchHit] = []
    for item in search[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        if not title:
            continue
        snippet_html = str(item.get("snippet") or "")
        snippet = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", snippet_html)).split())
        page_title = title.replace(" ", "_")
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page_title, safe="()!$&'*,;=:@-._~")
        timestamp = str(item.get("timestamp") or "")[:10] or None
        hits.append(SearchHit(
            title=title,
            url=url,
            snippet=snippet[:400],
            engine="wikipedia",
            position=len(hits) + 1,
            published_at=timestamp,
            query=query,
        ))
    return hits


def _pace_api_calls(min_interval: float = 1.0) -> None:
    """Serialize calls and leave at least one second between API requests."""
    global _LAST_CALL
    with _RATE_LOCK:
        delay = min_interval - (time.monotonic() - _LAST_CALL)
        if delay > 0:
            time.sleep(delay)
        _LAST_CALL = time.monotonic()


class WikipediaBackend(SearchBackend):
    name = "wikipedia"
    handled = {"date_from": POST, "date_to": POST, "include_domains": POST,
               "exclude_domains": UNSUPPORTED, "file_type": UNSUPPORTED,
               "language": UNSUPPORTED, "region": UNSUPPORTED}

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any) -> List[SearchHit]:
        timeout = 15
        if getattr(context, "config", None) is not None:
            try:
                timeout = max(1, int(context.config.get("search_timeout_seconds", 45)))
            except (TypeError, ValueError):
                pass
        params = urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": "0",
            "srlimit": min(max(1, limit), 50),
            "srprop": "timestamp",
            "format": "json",
            "formatversion": "2",
        })
        _pace_api_calls()
        try:
            text = fetch_url_text(
                f"{ENDPOINT}?{params}",
                timeout=timeout,
                max_bytes=2 * 1024 * 1024,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
            payload = json.loads(text)
        except Exception as exc:
            raise SourceUnavailableError("Wikipedia search API failed", detail=str(exc)[:250]) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("query"), dict):
            raise SourceUnavailableError("Wikipedia returned an invalid API response",
                                         detail="invalid_json_shape")
        return parse_wikipedia_payload(payload, query, limit * 2)
