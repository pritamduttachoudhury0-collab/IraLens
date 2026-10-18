# -*- coding: utf-8 -*-
"""Optional paid search API used only as an emergency fallback.

This backend is not enabled by default. Configure `backup_search_provider` and
`backup_search_api_key` (or the corresponding IRALENS_* environment variables)
to allow one final, explicit provider call after the free sources fail. The
configured key is sent only in an HTTPS header, never stored in health state or
included in diagnostic output.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Dict, List, Optional

from ...errors import SourceUnavailableError
from ...fetch import fetch_url_text
from ...search.schema import SearchFilters, SearchHit
from ...settings import Settings
from .base import POST, UNSUPPORTED, SearchBackend

_PROVIDER_URLS = {
    "brave": "https://api.search.brave.com/res/v1/web/search",
    "serper": "https://google.serper.dev/search",
    "tavily": "https://api.tavily.com/search",
}


def _records(provider: str, payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if provider == "brave":
        web = payload.get("web")
        items = web.get("results", []) if isinstance(web, dict) else []
    elif provider == "serper":
        items = payload.get("organic", [])
    else:
        items = payload.get("results", [])
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def parse_paid_payload(provider: str, payload: Any, query: str, limit: int) -> List[SearchHit]:
    """Normalize the three supported web-search response formats."""
    if provider not in _PROVIDER_URLS:
        return []
    results: List[SearchHit] = []
    for item in _records(provider, payload)[:max(0, limit)]:
        url = str(item.get("url") or item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        snippet = item.get("description") or item.get("snippet") or item.get("content") or ""
        published = (item.get("page_age") or item.get("date") or
                     item.get("published_date") or item.get("publishedDate"))
        results.append(SearchHit(
            title=title[:200],
            url=url,
            snippet=str(snippet).strip()[:400],
            engine="paid-backup",
            position=len(results) + 1,
            published_at=str(published)[:10] if published else None,
            query=query,
        ))
    return results


class PaidSearchBackupBackend(SearchBackend):
    name = "paid-backup"
    handled = {"date_from": POST, "date_to": POST, "include_domains": POST,
               "exclude_domains": POST, "file_type": POST,
               "language": UNSUPPORTED, "region": UNSUPPORTED}

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        settings = Settings.from_config(getattr(context, "config", None))
        provider = settings.backup_search_provider.strip().lower()
        key = settings.backup_search_api_key.strip()
        if provider not in _PROVIDER_URLS or not key:
            raise SourceUnavailableError(
                "paid search backup is not configured",
                hint="set IRALENS_BACKUP_SEARCH_PROVIDER and IRALENS_BACKUP_SEARCH_API_KEY",
                detail="backup_not_configured",
            )

        endpoint = _PROVIDER_URLS[provider]
        count = min(max(1, limit * 2), 50)
        headers = {"Accept": "application/json", "User-Agent": "IraLens/0.1"}
        body: Optional[bytes] = None
        if provider == "brave":
            endpoint += "?" + urllib.parse.urlencode({"q": query, "count": count})
            headers["X-Subscription-Token"] = key
        elif provider == "serper":
            body = json.dumps({"q": query, "num": count}).encode("utf-8")
            headers.update({"Content-Type": "application/json", "X-API-KEY": key})
        else:
            body = json.dumps({"query": query, "max_results": count}).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {key}"})

        timeout = max(1, int(settings.backup_search_timeout_seconds))
        try:
            text = fetch_url_text(endpoint, timeout=timeout, max_bytes=2 * 1024 * 1024,
                                  headers=headers, data=body, same_origin_redirects=True)
            payload = json.loads(text)
        except Exception as exc:
            # Do not echo headers, credentials, request body, or query into a
            # public error. HTTP codes and error types are enough to diagnose.
            status = getattr(exc, "detail", "") or type(exc).__name__
            safe_detail = " ".join(str(status).split())[:120]
            raise SourceUnavailableError("paid search backup request failed",
                                         detail=safe_detail) from exc
        if not isinstance(payload, dict):
            raise SourceUnavailableError("paid search backup returned an invalid response",
                                         detail="invalid_json_shape")
        provider_error = payload.get("error")
        if provider_error:
            raise SourceUnavailableError("paid search backup provider returned an error",
                                         detail="provider_error")
        return parse_paid_payload(provider, payload, query, limit * 2)
