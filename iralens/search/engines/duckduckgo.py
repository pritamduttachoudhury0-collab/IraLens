# -*- coding: utf-8 -*-
"""DuckDuckGo backend (html and lite endpoints) driven through the browser engine.

Parsing is a pure function of the HTML (`parse_results_page`), so it is tested
offline against fixtures in tests/fixtures/search/. The network step is
`fetch_rendered_html`.

Filter handling: region -> native (`kl` URL param); include_domains -> emulated
(`site:` operator, first domain only); file_type -> emulated (`filetype:`).
Everything else is post-filtered or unsupported.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from ...errors import ExtractionError, SourceUnavailableError
from ...reliability import classify_block
from ...search.schema import SearchFilters, SearchHit
from .base import EMULATED, NATIVE, POST, UNSUPPORTED, SearchBackend, fetch_rendered_html

ENDPOINTS = (
    "https://html.duckduckgo.com/html/?q={q}",
    "https://lite.duckduckgo.com/lite/?q={q}",
)
_NO_RESULTS_MARKERS = ("no results", "did not match any documents", "no more results")


@dataclass
class ParsedPage:
    hits: List[Dict[str, str]] = field(default_factory=list)
    block: Optional[str] = None      # captcha | rate_limited | None
    looks_like_serp: bool = False


def decode_result_url(href: str) -> str:
    """DuckDuckGo wraps targets as //duckduckgo.com/l/?uddg=<encoded>."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urllib.parse.urlsplit(href)
    if parts.netloc.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parts.query).get("uddg", [""])[0]
        if target:
            return target
    return href


class _SerpParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: List[Dict[str, str]] = []
        self._title_open = False
        self._title_buf: List[str] = []
        self._snippet_tag: Optional[str] = None
        self._snippet_depth = 0
        self._snippet_buf: List[str] = []
        self.text_all: List[str] = []
        self.saw_serp_marker = False

    @staticmethod
    def _classes(attrs) -> str:
        return " ".join((dict(attrs).get("class") or "").split())

    def handle_starttag(self, tag, attrs):
        cls = self._classes(attrs)
        if "duckduckgo" in (dict(attrs).get("class") or "") or "result__" in cls or "result-" in cls:
            self.saw_serp_marker = True
        if tag == "a" and ("result__a" in cls or "result-link" in cls):
            self._title_open = True
            self._title_buf = []
            href = dict(attrs).get("href") or ""
            self.hits.append({"title": "", "url": decode_result_url(href), "snippet": ""})
        elif self._snippet_tag is None and ("result__snippet" in cls or "result-snippet" in cls):
            self._snippet_tag = tag
            self._snippet_depth = 1
            self._snippet_buf = []
        elif self._snippet_tag is not None and tag == self._snippet_tag:
            self._snippet_depth += 1

    def handle_endtag(self, tag):
        if self._title_open and tag == "a":
            self._title_open = False
            if self.hits and not self.hits[-1]["title"]:
                self.hits[-1]["title"] = " ".join("".join(self._title_buf).split())
        if self._snippet_tag is not None and tag == self._snippet_tag:
            self._snippet_depth -= 1
            if self._snippet_depth == 0:
                if self.hits:
                    self.hits[-1]["snippet"] = " ".join("".join(self._snippet_buf).split())
                self._snippet_tag = None

    def handle_data(self, data):
        if self._title_open:
            self._title_buf.append(data)
        if self._snippet_tag is not None:
            self._snippet_buf.append(data)
        if data.strip():
            self.text_all.append(data.strip())


def parse_results_page(html: str) -> ParsedPage:
    parser = _SerpParser()
    parser.feed(html or "")
    parser.close()
    hits = [h for h in parser.hits if h["url"].startswith(("http://", "https://")) and h["title"]]
    page = ParsedPage(hits=hits, looks_like_serp=parser.saw_serp_marker)
    if not hits:
        page.block = classify_block(html or "")
    return page


class DuckDuckGoBackend(SearchBackend):
    name = "duckduckgo"
    handled = {"region": NATIVE, "include_domains": EMULATED, "file_type": EMULATED,
               "date_from": POST, "date_to": POST, "exclude_domains": POST, "language": UNSUPPORTED}

    def build_query(self, query: str, filters: SearchFilters) -> str:
        parts = [query]
        if filters.include_domains:
            parts.insert(0, f"site:{filters.include_domains[0]}")
        if filters.file_type:
            parts.append(f"filetype:{filters.file_type}")
        return " ".join(parts)

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any) -> List[SearchHit]:
        timeout = int(context.config.get("search_timeout_seconds", 45)) if context.config else 45
        encoded = urllib.parse.quote_plus(self.build_query(query, filters))
        region = f"&kl={urllib.parse.quote_plus(filters.region)}" if filters.region else ""
        last_error: Optional[Exception] = None
        for endpoint in ENDPOINTS:
            try:
                html = fetch_rendered_html(context, endpoint.format(q=encoded) + region, timeout)
            except Exception as exc:  # engine errors are classified by the caller
                last_error = exc
                continue
            page = parse_results_page(html)
            if page.hits:
                return [
                    SearchHit(title=h["title"], url=h["url"], snippet=h["snippet"][:400],
                              engine=self.name, position=i + 1, query=query)
                    for i, h in enumerate(page.hits[: limit * 2])
                ]
            if page.block:
                raise SourceUnavailableError(
                    f"{self.name} blocked the request ({page.block})",
                    hint="wait before retrying, or use another engine",
                    detail=page.block,
                )
            low = (html or "").lower()
            if any(m in low for m in _NO_RESULTS_MARKERS):
                return []
            if page.looks_like_serp:
                raise ExtractionError(f"{self.name} results layout not recognized", detail="layout_changed")
            last_error = ExtractionError(f"{self.name} returned no parseable page")
        if last_error is not None:
            raise SourceUnavailableError(f"{self.name} search failed", detail=str(last_error))
        return []
