# -*- coding: utf-8 -*-
"""Semantic search backend (Exa via the mcporter bridge CLI).

Requires the user to configure the bridge (see sources/search.py health hint).
The bridge tool is called with only the arguments it is known to accept
(query, numResults). Domain and date filters are therefore post-filtered
rather than sent as arguments the tool may not support.
"""

from __future__ import annotations

import json
import re
import shutil
from typing import Any, Dict, List

from ...errors import SourceUnavailableError
from ...proc import run_cli
from ...search.schema import SearchFilters, SearchHit
from .base import POST, UNSUPPORTED, SearchBackend


def parse_exa_payload(stdout: str, query: str, limit: int) -> List[SearchHit]:
    payload = _parse_jsonish(stdout)
    items = _find_result_list(payload)
    hits: List[SearchHit] = []
    for i, item in enumerate(items[:limit]):
        url = item.get("url") or item.get("link") or ""
        if not url:
            continue
        published = item.get("publishedDate") or item.get("published_date") or None
        hits.append(SearchHit(
            title=item.get("title") or url,
            url=url,
            snippet=(item.get("text") or item.get("snippet") or "")[:400],
            engine="semantic-search",
            position=i + 1,
            published_at=str(published)[:10] if published else None,
            query=query,
            raw={k: v for k, v in item.items() if k not in ("title", "url", "text", "snippet")},
        ))
    return hits


class ExaBackend(SearchBackend):
    name = "semantic-search"
    handled = {"date_from": POST, "date_to": POST, "include_domains": POST,
               "exclude_domains": POST, "file_type": POST, "language": UNSUPPORTED, "region": UNSUPPORTED}

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any) -> List[SearchHit]:
        if not shutil.which("mcporter"):
            raise SourceUnavailableError("semantic-search bridge CLI not installed")
        expr = f"exa.web_search_exa(query: {json.dumps(query)}, numResults: {int(limit)})"
        result = run_cli(["mcporter", "call", expr], timeout=60)
        if not result.ok:
            raise SourceUnavailableError("semantic search call failed", detail=result.stderr[:300])
        return parse_exa_payload(result.stdout, query, limit * 2)


def _parse_jsonish(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise SourceUnavailableError("search bridge returned unparseable output")


def _find_result_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "hits"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        for value in payload.values():
            found = _find_result_list(value)
            if found:
                return found
    return []
