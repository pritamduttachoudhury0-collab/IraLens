# -*- coding: utf-8 -*-
"""arXiv's public Atom API, routed only for scholarly queries.

This is a specialist source, not a general search engine. It needs no API
key. Requests are paced at least three seconds apart per process, as required
by arXiv's published API etiquette. The parser is offline-testable; live
reachability and current terms remain operator-verification items.
"""
from __future__ import annotations

import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from ...errors import IraLensError, SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.queryfocus import split_subject
from ...search.querytext import quoted_phrases, tokenize
from ...search.schema import SearchFilters, SearchHit
from .base import POST, SearchBackend

ENDPOINT = "https://export.arxiv.org/api/query"
_HOST = "export.arxiv.org"
_ATOM = "http://www.w3.org/2005/Atom"
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024
_STOPWORDS = frozenset("""
a an and are as at be by can for from how in into is it of on or the this to was what when where which who with
""".split())


def _subject(query: str) -> str:
    subject, _shape = split_subject(query)
    return subject.strip() or query.strip()


def _search_expression(query: str) -> str:
    """Build a conservative arXiv expression from the subject, not request noise."""
    subject = _subject(query)
    phrases = quoted_phrases(subject)
    if phrases:
        phrase = phrases[0].replace('"', "")
        return f'ti:"{phrase}"'
    terms: List[str] = []
    for token in tokenize(subject):
        low = token.lower()
        if low in _STOPWORDS or len(low) < 3 or not re.fullmatch(r"[\w.+#-]+", low):
            continue
        if low not in terms:
            terms.append(low)
    terms = terms[:8]
    if not terms:
        return f'all:{subject}'
    # An AND over the subject tokens is much less noisy than sending a long
    # natural-language request suffix to the index. urllib encodes the
    # expression as one query parameter below.
    return " AND ".join(f"all:{term}" for term in terms)


def _local_text(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]*>", " ", value or ""))
    text = " ".join(text.split())
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def parse_arxiv_feed(payload: str, query: str, limit: int) -> List[SearchHit]:
    """Parse the Atom feed arXiv returns; no request is made here."""
    try:
        root = ET.fromstring(payload or "")
    except ET.ParseError as exc:
        raise SourceUnavailableError("arXiv returned an invalid Atom feed",
                                     detail="invalid_atom") from exc
    hits: List[SearchHit] = []
    for entry in root.findall(f"{{{_ATOM}}}entry")[:max(0, limit)]:
        title = _local_text(entry.findtext(f"{{{_ATOM}}}title", ""))
        url = entry.findtext(f"{{{_ATOM}}}id", "").strip()
        if url.startswith("http://arxiv.org/"):
            url = "https://arxiv.org/" + url[len("http://arxiv.org/"):]
        summary = _local_text(entry.findtext(f"{{{_ATOM}}}summary", ""))
        published_raw = entry.findtext(f"{{{_ATOM}}}published", "")[:10]
        published = published_raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_raw) else None
        if not title or not url.startswith("https://arxiv.org/"):
            continue
        authors = [
            _local_text(node.findtext(f"{{{_ATOM}}}name", ""))
            for node in entry.findall(f"{{{_ATOM}}}author")[:3]
        ]
        authors = [name for name in authors if name]
        snippet = summary or ", ".join(authors)
        raw: Dict[str, Any] = {"provider": "arxiv"}
        if published:
            raw["date_evidence"] = "provider:arxiv"
        hits.append(SearchHit(
            title=title[:200], url=url, snippet=snippet[:400], engine="arxiv",
            position=len(hits) + 1, published_at=published,
            date_kind="publication" if published else "", query=query, raw=raw,
        ))
    return hits


class ArxivBackend(SearchBackend):
    name = "arxiv"
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
        max_results = min(max(1, limit * 2), 50)
        params = urllib.parse.urlencode({
            "search_query": _search_expression(query),
            "start": max(0, int(offset)),
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        })
        HOST_RATE_LIMITER.set_interval(_HOST, 3.0)
        HOST_RATE_LIMITER.wait(_HOST)
        try:
            text = fetch_url_text(
                f"{ENDPOINT}?{params}", timeout=timeout, max_bytes=_MAX_RESPONSE_BYTES,
                headers={"Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.1",
                         "User-Agent": "IraLens/0.3 (https://github.com/pritamduttachoudhury0-collab/IraLens)"},
            )
        except IraLensError:
            raise
        except Exception as exc:
            raise SourceUnavailableError("arXiv search request failed",
                                         detail=type(exc).__name__) from exc
        return parse_arxiv_feed(text, query, limit * 2)
