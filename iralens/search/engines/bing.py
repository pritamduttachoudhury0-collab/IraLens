# -*- coding: utf-8 -*-
"""Bing backend driven through the browser engine.

Status: EXPERIMENTAL. The HTML parser is tested offline against a fixture that
mirrors Bing's `li.b_algo` result structure. It has NOT been verified against
live Bing from this environment (the sandbox cannot reach bing.com). The
fallback layer treats it like any other engine and reports a layout change if
the structure stops matching. See KNOWN_LIMITATIONS.md.

Filter handling: language -> native (`setlang`); include_domains -> emulated
(`site:`, first domain); file_type -> emulated (`filetype:`).
"""

from __future__ import annotations

import base64
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from ...errors import ExtractionError, SourceUnavailableError
from ...reliability import classify_block
from ...search.schema import SearchFilters, SearchHit
from .base import EMULATED, NATIVE, POST, UNSUPPORTED, SearchBackend, fetch_serp_html

ENDPOINT = "https://www.bing.com/search?q={q}"
_NO_RESULTS_MARKERS = ("there are no results", "did not match any documents")


def decode_bing_url(href: str) -> str:
    """Bing click-tracking links carry the target in `u=a1<base64url>`."""
    parts = urllib.parse.urlsplit(href)
    if parts.netloc.endswith("bing.com") and parts.path.startswith("/ck/"):
        token = urllib.parse.parse_qs(parts.query).get("u", [""])[0]
        if token.startswith("a1"):
            payload = token[2:]
            payload += "=" * (-len(payload) % 4)
            try:
                target = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
                if target.startswith(("http://", "https://")):
                    return target
            except (ValueError, UnicodeDecodeError):
                pass
    return href


@dataclass
class ParsedPage:
    hits: List[Dict[str, str]] = field(default_factory=list)
    block: Optional[str] = None
    looks_like_serp: bool = False


class _BingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: List[Dict[str, str]] = []
        self.saw_serp = False
        self._li_depth = 0          # depth inside the current b_algo block
        self._in_block = False
        self._in_h2 = False
        self._title_buf: List[str] = []
        self._in_p = False
        self._p_buf: List[str] = []
        self._current: Optional[Dict[str, str]] = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        cls = attr.get("class") or ""
        if "b_algo" in cls.split():
            self.saw_serp = True
            self._in_block = True
            self._li_depth = 1
            self._current = {"title": "", "url": "", "snippet": ""}
            return
        if not self._in_block:
            return
        if tag == "li":
            self._li_depth += 1
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and self._current is not None and not self._current["url"]:
            self._current["url"] = decode_bing_url(attr.get("href") or "")
            self._title_buf = []
        elif tag == "p" and self._current is not None and self._current["url"] and not self._current["snippet"]:
            self._in_p = True
            self._p_buf = []

    def handle_endtag(self, tag):
        if not self._in_block:
            return
        if tag == "h2":
            self._in_h2 = False
            if self._current is not None and self._current["url"] and not self._current["title"]:
                self._current["title"] = " ".join("".join(self._title_buf).split())
        elif tag == "p" and self._in_p and self._current is not None:
            self._in_p = False
            self._current["snippet"] = " ".join("".join(self._p_buf).split())
        elif tag == "li":
            self._li_depth -= 1
            if self._li_depth == 0:
                if self._current and self._current["url"]:
                    self.hits.append(self._current)
                self._in_block = False
                self._current = None

    def handle_data(self, data):
        if self._in_h2 and self._in_block:
            self._title_buf.append(data)
        if self._in_p:
            self._p_buf.append(data)


def parse_results_page(html: str) -> ParsedPage:
    parser = _BingParser()
    parser.feed(html or "")
    parser.close()
    hits = [h for h in parser.hits if h["url"].startswith(("http://", "https://")) and h["title"]]
    page = ParsedPage(hits=hits, looks_like_serp=parser.saw_serp)
    if not hits:
        page.block = classify_block(html or "")
    return page


class BingBackend(SearchBackend):
    name = "bing"
    handled = {"language": NATIVE, "include_domains": EMULATED, "file_type": EMULATED,
               "date_from": POST, "date_to": POST, "exclude_domains": POST, "region": UNSUPPORTED}

    def build_query(self, query: str, filters: SearchFilters) -> str:
        parts = [query]
        if filters.include_domains:
            parts.insert(0, f"site:{filters.include_domains[0]}")
        if filters.file_type:
            parts.append(f"filetype:{filters.file_type}")
        return " ".join(parts)

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any) -> List[SearchHit]:
        timeout = int(context.config.get("search_timeout_seconds", 45)) if context.config else 45
        # `query` is the built query text (D-076 transport: direct HTTP first,
        # rendered engine only as fallback — see fetch_serp_html).
        url = ENDPOINT.format(q=urllib.parse.quote_plus(query))
        if filters.language:
            url += f"&setlang={urllib.parse.quote_plus(filters.language)}"
        html = fetch_serp_html(context, url, timeout)
        page = parse_results_page(html)
        if page.hits:
            return [
                SearchHit(title=h["title"], url=h["url"], snippet=h["snippet"][:400],
                          engine=self.name, position=i + 1, query=query)
                for i, h in enumerate(page.hits[: limit * 2])
            ]
        if page.block:
            raise SourceUnavailableError(f"{self.name} blocked the request ({page.block})",
                                         hint="wait before retrying, or use another engine",
                                         detail=page.block)
        if any(m in (html or "").lower() for m in _NO_RESULTS_MARKERS):
            return []
        if page.looks_like_serp:
            raise ExtractionError(f"{self.name} results layout not recognized", detail="layout_changed")
        raise SourceUnavailableError(f"{self.name} returned no parseable page")
