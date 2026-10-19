# -*- coding: utf-8 -*-
"""Stack Exchange's public API, routed for software/technical questions.

The API is keyless for public Stack Overflow content, subject to its published
quota and attribution rules. It is not used as a general web index. Search
results link to their original question pages; the API supplies the question
body as a ranking/evidence snippet. Live provider behavior is not measured in
the restricted development environment.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any, Dict, List

from ...errors import IraLensError, SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.queryfocus import split_subject
from ...search.schema import SearchFilters, SearchHit
from .base import POST, SearchBackend

ENDPOINT = "https://api.stackexchange.com/2.3/search/advanced"
_HOST = "api.stackexchange.com"
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024


def _plain_text(value: Any, limit: int = 800) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())[:limit]


def parse_stackexchange_payload(payload: Any, query: str, limit: int) -> List[SearchHit]:
    """Normalize Stack Exchange advanced-search results without I/O."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    hits: List[SearchHit] = []
    for item in items[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        title = _plain_text(item.get("title"), 200)
        url = str(item.get("link") or "").strip()
        if not title or not url.startswith("https://stackoverflow.com/"):
            continue
        body = _plain_text(item.get("body"), 800)
        tags = item.get("tags")
        tag_text = ", ".join(str(tag) for tag in tags[:5]) if isinstance(tags, list) else ""
        details = " · ".join(part for part in (
            "accepted answer" if item.get("is_answered") else "unanswered",
            f"score {item.get('score')}" if item.get("score") is not None else "",
            tag_text,
        ) if part)
        snippet = body or details
        raw: Dict[str, Any] = {
            "provider": "stackexchange",
            "answered": bool(item.get("is_answered")),
            "score": item.get("score"),
            "tags": tags[:10] if isinstance(tags, list) else [],
        }
        hits.append(SearchHit(
            title=title, url=url, snippet=snippet[:400], engine="stackexchange",
            position=len(hits) + 1, query=query, raw=raw,
        ))
    return hits


class StackExchangeBackend(SearchBackend):
    name = "stackexchange"
    paginated = True
    handled = {"date_from": POST, "date_to": POST, "include_domains": POST,
               "exclude_domains": POST, "file_type": POST,
               "language": POST, "region": POST}

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        settings = getattr(context, "config", None)
        try:
            timeout = max(1, int(settings.get("search_timeout_seconds", 20))) if settings else 20
        except (TypeError, ValueError):
            timeout = 20
        page_size = min(max(1, limit * 2), 100)
        subject, _shape = split_subject(query)
        params = urllib.parse.urlencode({
            "site": "stackoverflow",
            "q": subject.strip() or query,
            "pagesize": page_size,
            "page": max(1, int(offset) // page_size + 1),
            "sort": "relevance",
            "order": "desc",
            "filter": "withbody",
        })
        HOST_RATE_LIMITER.set_interval(_HOST, 1.0)
        HOST_RATE_LIMITER.wait(_HOST)
        try:
            text = fetch_url_text(
                f"{ENDPOINT}?{params}", timeout=timeout, max_bytes=_MAX_RESPONSE_BYTES,
                headers={"Accept": "application/json",
                         "User-Agent": "IraLens/0.3 (https://github.com/pritamduttachoudhury0-collab/IraLens)"},
            )
            payload = json.loads(text)
        except IraLensError:
            raise
        except ValueError as exc:
            raise SourceUnavailableError("Stack Exchange returned an invalid response",
                                         detail="invalid_json") from exc
        except Exception as exc:
            raise SourceUnavailableError("Stack Exchange search request failed",
                                         detail=type(exc).__name__) from exc
        if not isinstance(payload, dict):
            raise SourceUnavailableError("Stack Exchange returned an invalid response",
                                         detail="invalid_json_shape")
        if payload.get("error_id"):
            error_name = str(payload.get("error_name") or "api_error")[:60]
            if error_name in ("throttle_violation", "quota_exceeded"):
                raise SourceUnavailableError("Stack Exchange API quota is exhausted",
                                             detail="rate limit exceeded")
            raise SourceUnavailableError("Stack Exchange API returned an error",
                                         detail=error_name)
        # A partial page at the end of the public quota is still useful; the
        # response's quota is kept only as bounded provenance.
        hits = parse_stackexchange_payload(payload, query, limit * 2)
        skip = max(0, int(offset)) % page_size
        hits = hits[skip:skip + limit * 2]
        for hit in hits:
            quota = payload.get("quota_remaining")
            if isinstance(quota, int):
                hit.raw["quota_remaining"] = max(0, min(quota, 100000))
        return hits
