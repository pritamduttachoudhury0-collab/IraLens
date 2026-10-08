# -*- coding: utf-8 -*-
"""Unified web search.

One capability, ordered backends:
  1. semantic-search — Exa via an MCP bridge CLI (if the user configured it).
  2. browser-search — the shared engine drives a public search engine
     (DuckDuckGo HTML) and parses the result page.

Half IraLens never builds its own index; it uses existing search engines.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.parse
from typing import Any, Dict, List, TYPE_CHECKING

from ..errors import ExtractionError, SourceUnavailableError
from ..model import SearchResult
from ..proc import run_cli
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_DDG_ENDPOINTS = (
    "https://html.duckduckgo.com/html/?q={q}",
    "https://lite.duckduckgo.com/lite/?q={q}",
)

#: JS executed inside the engine to scrape a DuckDuckGo results page.
_DDG_EXTRACT_JS = r"""
JSON.stringify((() => {
  const out = [];
  const decode = (href) => {
    try {
      const u = new URL(href, location.href);
      const uddg = u.searchParams.get('uddg');
      return uddg ? decodeURIComponent(uddg) : u.href;
    } catch (e) { return href; }
  };
  const results = document.querySelectorAll('a.result__a');
  if (results.length) {
    results.forEach((a, i) => {
      const snippetEl = a.closest('.result, .web-result, tr')?.querySelector('.result__snippet');
      out.push({title: a.textContent.trim(), url: decode(a.href),
                snippet: snippetEl ? snippetEl.textContent.trim() : ''});
    });
    return out;
  }
  document.querySelectorAll('a[href]').forEach((a) => {
    const href = a.getAttribute('href') || '';
    const text = a.textContent.trim();
    if (!text || text.length < 4) return;
    const url = decode(a.href);
    if (!/^https?:/i.test(url)) return;
    if (/duckduckgo\.com/.test(url)) return;
    out.push({title: text, url, snippet: ''});
  });
  return out.slice(0, 30);
})())
"""


class SearchSource(Source):
    name = "web-search"
    description = "Web search"
    backends = ["semantic-search", "browser-search"]
    tier = 0
    operations = {
        "query": {
            "description": "Search the open web and return ranked results",
            "params": {"query": "search text", "limit": "max results (default 8)"},
        }
    }

    def health(self, context: "Context") -> SourceHealth:
        if shutil.which("mcporter"):
            self.active_backend = "semantic-search"
            return SourceHealth(
                "ok",
                "semantic search bridge present; browser search also available as fallback",
                self.active_backend,
            )
        self.active_backend = "browser-search"
        engine_health = context.engine().health()
        if engine_health.get("status") == "off":
            self.active_backend = None
            return SourceHealth(
                "off",
                "no search backend: install the browser engine (halfiralens install-engine) "
                "or configure the semantic-search bridge (npm i -g mcporter && "
                "mcporter config add exa https://mcp.exa.ai/mcp --scope home)",
            )
        return SourceHealth("ok", "browser-driven search engine", self.active_backend)

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op != "query":
            return super().fetch(op, params, context)
        query = (params.get("query") or "").strip()
        if not query:
            raise ExtractionError("search requires a non-empty 'query'")
        limit = int(params.get("limit") or 8)

        preferred = str(params.get("backend") or "")
        chain = self.ordered_backends(context)
        if preferred:
            chain = [b for b in chain if b.startswith(preferred)] or chain

        failures: List[str] = []
        for backend in chain:
            try:
                if backend == "semantic-search":
                    results = self._search_exa(query, limit)
                else:
                    results = self._search_via_engine(query, limit, context)
                if results:
                    return [
                        r.to_artifact(query, f"search:{backend}") for r in results[:limit]
                    ]
                failures.append(f"{backend}: no results")
            except SourceUnavailableError as exc:
                failures.append(f"{backend}: {exc.message}")
            except Exception as exc:  # keep the chain alive
                failures.append(f"{backend}: {exc}")
        raise SourceUnavailableError(
            "web search failed on every backend",
            hint="check engine status with: halfiralens doctor",
            detail="; ".join(failures),
        )

    # ------------------------------------------------------------ backends
    @staticmethod
    def _search_exa(query: str, limit: int) -> List[SearchResult]:
        if not shutil.which("mcporter"):
            raise SourceUnavailableError("semantic-search bridge CLI not installed")
        expr = f'exa.web_search_exa(query: {json.dumps(query)}, numResults: {limit})'
        result = run_cli(["mcporter", "call", expr], timeout=60)
        if not result.ok:
            raise SourceUnavailableError("semantic search call failed", detail=result.stderr[:300])
        payload = _parse_jsonish(result.stdout)
        items = _find_result_list(payload)
        out: List[SearchResult] = []
        for i, item in enumerate(items[:limit]):
            url = item.get("url") or item.get("link") or ""
            if not url:
                continue
            out.append(
                SearchResult(
                    title=item.get("title") or url,
                    url=url,
                    snippet=(item.get("text") or item.get("snippet") or "")[:400],
                    rank=i + 1,
                    extra={k: v for k, v in item.items() if k not in ("title", "url", "text", "snippet")},
                )
            )
        return out

    @staticmethod
    def _search_via_engine(query: str, limit: int, context: "Context") -> List[SearchResult]:
        engine = context.engine()
        encoded = urllib.parse.quote_plus(query)
        last_error: Exception | None = None
        for endpoint in _DDG_ENDPOINTS:
            try:
                engine.navigate(endpoint.format(q=encoded), wait_until="domcontentloaded", timeout=45)
                raw = engine.evaluate_js(_DDG_EXTRACT_JS)
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, list) and data:
                    results = []
                    for i, item in enumerate(data[: limit * 2]):
                        url = (item.get("url") or "").strip()
                        if not url.startswith(("http://", "https://")):
                            continue
                        results.append(
                            SearchResult(
                                title=item.get("title") or url,
                                url=url,
                                snippet=item.get("snippet") or "",
                                rank=i + 1,
                            )
                        )
                        if len(results) >= limit:
                            break
                    if results:
                        return results
                last_error = ExtractionError("search engine returned no parseable results")
            except Exception as exc:
                last_error = exc
        if last_error:
            raise SourceUnavailableError(
                "browser-driven search failed",
                hint="the search engine may be rate-limiting this network",
                detail=str(last_error),
            )
        return []


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
    """Locate the results array inside a bridge response of unknown shape."""
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
