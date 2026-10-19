# -*- coding: utf-8 -*-
"""GitHub's public REST search API for explicitly GitHub-scoped queries.

Only repository, issue, and release metadata are queried; code search is not
used because GitHub requires authentication for that endpoint. Anonymous API
quota is limited, so this source is injected only for GitHub-specific intent
and can also be selected explicitly. A configured IRALENS_GITHUB_TOKEN is
read from the private config/env path, sent only over HTTPS, and never logged.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from ...errors import IraLensError, SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.querytext import parse_site_scopes
from ...search.schema import SearchFilters, SearchHit
from .base import POST, SearchBackend

_API = "https://api.github.com"
_HOST = "api.github.com"
_USER_AGENT = "IraLens/0.3 (https://github.com/pritamduttachoudhury0-collab/IraLens)"
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def _repo_scope(query: str) -> Optional[str]:
    scopes, _cleaned = parse_site_scopes(query or "")
    for scope in scopes:
        host, _, path = scope.partition("/")
        if host.casefold() == "github.com" and _REPOSITORY.fullmatch(path):
            return path
    return None


def _records(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _snippet(value: Any, limit: int = 400) -> str:
    import html

    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())[:limit]


def parse_github_payload(payload: Any, query: str, limit: int, *,
                         mode: str = "search") -> List[SearchHit]:
    """Normalize repository/issue/release API payloads without I/O."""
    if mode == "releases":
        items = payload if isinstance(payload, list) else []
    else:
        items = _records(payload)
    hits: List[SearchHit] = []
    for item in items[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        modified: Optional[str] = None
        if mode == "repositories":
            title = str(item.get("full_name") or item.get("name") or "").strip()
            url = str(item.get("html_url") or "").strip()
            description = _snippet(item.get("description"))
            license_data = item.get("license")
            license_name = (str(license_data.get("spdx_id") or "")
                            if isinstance(license_data, dict) else "")
            snippet = " · ".join(part for part in (description, f"License: {license_name}" if license_name else "") if part)
            date_raw = item.get("updated_at")
            modified = str(date_raw)[:10] if date_raw else None
            published = None
        elif mode == "releases":
            title = str(item.get("name") or item.get("tag_name") or "").strip()
            url = str(item.get("html_url") or "").strip()
            snippet = _snippet(item.get("body"))
            date_raw = item.get("published_at")
            published = str(date_raw)[:10] if date_raw else None
        else:
            title = str(item.get("title") or "").strip()
            url = str(item.get("html_url") or "").strip()
            snippet = _snippet(item.get("body"))
            date_raw = item.get("created_at")
            published = str(date_raw)[:10] if date_raw else None
        if not title or not url.startswith("https://github.com/"):
            continue
        if published and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
            published = None
        if modified and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", modified):
            modified = None
        raw: Dict[str, Any] = {"provider": "github", "kind": mode}
        for source, target in (("state", "state"), ("score", "provider_score"),
                               ("stargazers_count", "stars"), ("number", "number")):
            if item.get(source) is not None:
                raw[target] = item[source]
        hits.append(SearchHit(
            title=title[:200], url=url, snippet=snippet, engine="github-search",
            position=len(hits) + 1, published_at=published, modified_at=modified,
            date_kind="publication" if published else "revision" if modified else "",
            query=query, raw=raw,
        ))
    return hits


class GitHubSearchBackend(SearchBackend):
    name = "github-search"
    paginated = True
    handled = {"date_from": POST, "date_to": POST, "include_domains": POST,
               "exclude_domains": POST, "file_type": POST,
               "language": POST, "region": POST}

    def _token(self, context: Any) -> str:
        config = getattr(context, "config", None)
        try:
            value = config.get("github_token", "") if config is not None else ""
        except Exception:
            value = ""
        return str(value or os.environ.get("IRALENS_GITHUB_TOKEN", "")).strip()

    def _get_json(self, path: str, context: Any, timeout: int) -> Any:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT,
                   "X-GitHub-Api-Version": "2022-11-28"}
        token = self._token(context)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        HOST_RATE_LIMITER.set_interval(_HOST, 1.0)
        HOST_RATE_LIMITER.wait(_HOST)
        try:
            text = fetch_url_text(
                _API + path, timeout=timeout, max_bytes=_MAX_RESPONSE_BYTES,
                headers=headers, same_origin_redirects=True,
            )
            return json.loads(text)
        except IraLensError:
            raise
        except ValueError as exc:
            raise SourceUnavailableError("GitHub search returned an invalid response",
                                         detail="invalid_json") from exc
        except Exception as exc:
            raise SourceUnavailableError("GitHub search request failed",
                                         detail=type(exc).__name__) from exc

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        settings = getattr(context, "config", None)
        try:
            timeout = max(1, int(settings.get("search_timeout_seconds", 20))) if settings else 20
        except (TypeError, ValueError):
            timeout = 20
        count = min(max(1, limit * 2), 100)
        repo = _repo_scope(query)
        _scopes, cleaned = parse_site_scopes(query)
        text = cleaned.strip() or query.strip()
        low = text.casefold()
        page = max(1, int(offset) // count + 1)
        skip = max(0, int(offset)) % count

        def _page_hits(payload: Any, mode: str) -> List[SearchHit]:
            hits = parse_github_payload(payload, query, count, mode=mode)
            return hits[skip:skip + limit * 2]

        if repo and re.search(r"\b(release|releases|version|changelog)\b", low):
            path = f"/repos/{repo}/releases?" + urllib.parse.urlencode({"per_page": count, "page": page})
            return _page_hits(self._get_json(path, context, timeout), "releases")

        if repo and re.search(r"\b(issue|issues|bug|bugs|pull request|pull requests|pr|regression)\b", low):
            terms = re.sub(r"\bsite:github\.com/[^\s]+\b", " ", query, flags=re.IGNORECASE)
            terms = re.sub(r"\b(github|issues?|pull requests?|pr|open|closed)\b", " ", terms,
                           flags=re.IGNORECASE)
            is_pr = bool(re.search(r"\b(pull request|pull requests|pr)\b", low))
            search_q = f"repo:{repo} {terms.strip()} {'is:pr' if is_pr else 'is:issue'}"
            if not is_pr and re.search(r"\b(open|regression|bug|bugs)\b", low):
                search_q += " is:open"
            path = "/search/issues?" + urllib.parse.urlencode({
                "q": search_q, "per_page": count, "page": page, "sort": "updated", "order": "desc",
            })
            return _page_hits(self._get_json(path, context, timeout), "issues")

        if repo:
            path = f"/repos/{repo}"
            data = self._get_json(path, context, timeout)
            if not isinstance(data, dict):
                raise SourceUnavailableError("GitHub returned an invalid repository response",
                                             detail="invalid_json_shape")
            return _page_hits({"items": [data]}, "repositories")

        # This adapter is intent-routed only for GitHub-specific requests.
        # Use the official repository search API; authenticated code search is
        # deliberately out of scope.
        search_q = text
        path = "/search/repositories?" + urllib.parse.urlencode({
            "q": search_q, "per_page": count, "page": page, "sort": "stars", "order": "desc",
        })
        return _page_hits(self._get_json(path, context, timeout), "repositories")
