# -*- coding: utf-8 -*-
"""Crossref's public works API, routed only for scholarly queries.

Crossref is an official bibliographic API, not a general web index. It needs
no search key. The adapter is intentionally intent-routed so ordinary queries
are not sent to a specialist provider that cannot answer them. Parsing is a
pure function and is tested offline; live reachability and current API terms
must be checked on the operator's network.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from datetime import date
from typing import Any, Dict, List, Optional

from ...errors import IraLensError, SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.queryfocus import split_subject
from ...search.schema import SearchFilters, SearchHit
from .base import POST, SearchBackend

ENDPOINT = "https://api.crossref.org/works"
_HOST = "api.crossref.org"
_USER_AGENT = "IraLens/0.3 (https://github.com/pritamduttachoudhury0-collab/IraLens)"
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024


def _plain_text(value: Any, limit: int = 1000) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())[:limit]


def _title(item: Dict[str, Any]) -> str:
    titles = item.get("title")
    if isinstance(titles, list):
        return " ".join(str(titles[0] or "").split()) if titles else ""
    return " ".join(str(titles or "").split())


def _publication_date(item: Dict[str, Any]) -> Optional[str]:
    for key in ("published-print", "published-online", "issued"):
        value = item.get(key)
        parts = value.get("date-parts") if isinstance(value, dict) else None
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        try:
            # Do not invent January 1 when the provider gives only a year or
            # month. The freshness model accepts calendar dates, not ranges.
            if len(parts[0]) < 3:
                continue
            year, month, day = (int(parts[0][0]), int(parts[0][1]), int(parts[0][2]))
            return date(year, month, day).isoformat()
        except (TypeError, ValueError, IndexError):
            continue
    return None


def parse_crossref_payload(payload: Any, query: str, limit: int) -> List[SearchHit]:
    """Normalize a Crossref works response without performing network I/O."""
    message = payload.get("message") if isinstance(payload, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        return []
    hits: List[SearchHit] = []
    for item in items[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        title = _title(item)
        doi = str(item.get("DOI") or "").strip()
        url = str(item.get("URL") or "").strip()
        if not url and doi:
            url = "https://doi.org/" + urllib.parse.quote(doi, safe="/()")
        if not title or not url:
            continue
        authors = item.get("author")
        author_names: List[str] = []
        if isinstance(authors, list):
            for author in authors[:4]:
                if not isinstance(author, dict):
                    continue
                name = " ".join(str(author.get(k) or "") for k in ("given", "family")).strip()
                if name:
                    author_names.append(name)
        containers = item.get("container-title")
        journal = str(containers[0]) if isinstance(containers, list) and containers else ""
        abstract = _plain_text(item.get("abstract"), 800)
        details = "; ".join(part for part in (
            ", ".join(author_names), journal, _publication_date(item),
            ("DOI " + doi) if doi else "",
        ) if part)
        snippet = abstract or details
        published = _publication_date(item)
        raw: Dict[str, Any] = {"provider": "crossref", "type": str(item.get("type") or "")}
        if item.get("score") is not None:
            raw["provider_score"] = item.get("score")
        if published:
            raw["date_evidence"] = "provider:crossref"
        hits.append(SearchHit(
            title=title[:200], url=url, snippet=snippet[:400], engine="crossref",
            position=len(hits) + 1, published_at=published, date_kind="publication" if published else "",
            query=query, raw=raw,
        ))
    return hits


class CrossrefBackend(SearchBackend):
    name = "crossref"
    paginated = True
    handled = {"date_from": POST, "date_to": POST, "include_domains": POST,
               "exclude_domains": POST, "file_type": POST,
               "language": POST, "region": POST}

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        subject, _shape = split_subject(query)
        search_query = subject.strip() or query
        settings = getattr(context, "config", None)
        try:
            timeout = max(1, int(settings.get("search_timeout_seconds", 20))) if settings else 20
        except (TypeError, ValueError):
            timeout = 20
        rows = min(max(1, limit * 2), 50)
        params = urllib.parse.urlencode({
            "query.bibliographic": search_query,
            "rows": rows,
            "offset": max(0, int(offset)),
            "select": "DOI,title,URL,author,container-title,published-print,published-online,issued,abstract,score,type",
        })
        HOST_RATE_LIMITER.set_interval(_HOST, 1.0)
        HOST_RATE_LIMITER.wait(_HOST)
        try:
            text = fetch_url_text(
                f"{ENDPOINT}?{params}", timeout=timeout, max_bytes=_MAX_RESPONSE_BYTES,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
            payload = json.loads(text)
        except IraLensError:
            raise
        except ValueError as exc:
            raise SourceUnavailableError("Crossref returned an invalid response",
                                         detail="invalid_json") from exc
        except Exception as exc:
            raise SourceUnavailableError("Crossref search request failed",
                                         detail=type(exc).__name__) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            raise SourceUnavailableError("Crossref returned an invalid response",
                                         detail="invalid_json_shape")
        return parse_crossref_payload(payload, query, limit * 2)
