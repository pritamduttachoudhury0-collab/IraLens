# -*- coding: utf-8 -*-
"""Mojeek backend (keyless, tertiary default fallback).

Mojeek is an independent crawler-based engine with a keyless HTML search
surface (https://www.mojeek.com/search). It follows DuckDuckGo and Bing in the
default chain, so it is normally called only when a primary engine fails or
more independent sources are needed. The parser is fixture-tested offline;
live behavior remains network-dependent. A short timeout plus the shared
circuit breaker keeps a blocked endpoint from dominating latency. Review
Mojeek's terms before production use.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from ...errors import ExtractionError, SourceUnavailableError
from ...reliability import classify_block
from ...search.schema import SearchFilters, SearchHit
from .base import EMULATED, POST, UNSUPPORTED, SearchBackend, fetch_serp_html

ENDPOINT = "https://www.mojeek.com/search?q={q}"
_NO_RESULTS_MARKERS = ("no results found", "did not match any documents")


@dataclass
class ParsedPage:
    hits: List[Dict[str, str]] = field(default_factory=list)
    block: Optional[str] = None
    looks_like_serp: bool = False


class _MojeekParser(HTMLParser):
    """Mojeek results: <li> blocks containing an <h2><a> title and <p> snippet."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: List[Dict[str, str]] = []
        self.saw_serp = False
        self._current: Optional[Dict[str, str]] = None
        self._in_h2 = False
        self._in_a = False
        self._in_p = False
        self._buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        cls = attr.get("class") or ""
        if "results" in cls or tag == "ul":
            self.saw_serp = self.saw_serp or "result" in cls
        if tag == "h2":
            self._in_h2 = True
            self._current = {"title": "", "url": "", "snippet": ""}
        elif tag == "a" and self._in_h2 and self._current is not None and not self._current["url"]:
            self._current["url"] = attr.get("href") or ""
            self._in_a = True
            self._buf = []
        elif tag == "p" and self._current is not None and self._current["url"] and not self._current["snippet"]:
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a and self._current is not None:
            self._in_a = False
            self._current["title"] = " ".join("".join(self._buf).split())
        elif tag == "p" and self._in_p and self._current is not None:
            self._in_p = False
            self._current["snippet"] = " ".join("".join(self._buf).split())
            if self._current["url"].startswith(("http://", "https://")) and self._current["title"]:
                self.hits.append(self._current)
            self._current = None
        elif tag == "h2":
            self._in_h2 = False

    def handle_data(self, data):
        if self._in_a or self._in_p:
            self._buf.append(data)


def parse_results_page(html: str) -> ParsedPage:
    parser = _MojeekParser()
    parser.feed(html or "")
    parser.close()
    hits = [h for h in parser.hits if h["url"].startswith(("http://", "https://")) and h["title"]]
    page = ParsedPage(hits=hits, looks_like_serp=bool(hits) or parser.saw_serp)
    if not hits:
        page.block = classify_block(html or "")
    return page


class MojeekBackend(SearchBackend):
    name = "mojeek"
    paginated = True
    handled = {"include_domains": EMULATED, "file_type": EMULATED,
               "date_from": POST, "date_to": POST, "exclude_domains": POST,
               "language": UNSUPPORTED, "region": UNSUPPORTED}

    def build_query(self, query: str, filters: SearchFilters) -> str:
        parts = [query]
        if filters.include_domains:
            parts.insert(0, f"site:{filters.include_domains[0]}")
        return " ".join(parts)

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        config = getattr(context, "config", None)
        try:
            configured_timeout = max(1, int(config.get("search_timeout_seconds", 45))) if config else 45
        except (TypeError, ValueError):
            configured_timeout = 45
        # Mojeek is a fallback, so a blocked or slow endpoint must not consume
        # the general search deadline on each of the first breaker probes.
        timeout = min(configured_timeout, 8)
        url = ENDPOINT.format(q=urllib.parse.quote_plus(query))
        if offset:
            url += f"&s={int(offset)}"
        html = fetch_serp_html(context, url, timeout)
        page = parse_results_page(html)
        if page.hits:
            return [SearchHit(title=h["title"], url=h["url"], snippet=h["snippet"][:400],
                              engine=self.name, position=i + 1, query=query)
                    for i, h in enumerate(page.hits[: limit * 2])]
        if page.block:
            raise SourceUnavailableError(f"{self.name} blocked the request ({page.block})",
                                         hint="wait before retrying, or use another engine",
                                         detail=page.block)
        if any(m in (html or "").lower() for m in _NO_RESULTS_MARKERS):
            return []
        if page.looks_like_serp:
            raise ExtractionError(f"{self.name} results layout not recognized", detail="layout_changed")
        raise SourceUnavailableError(f"{self.name} returned no parseable page")
