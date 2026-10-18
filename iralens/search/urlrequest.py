# -*- coding: utf-8 -*-
"""A query that names a page is a read request, not a search request.

Give a search engine the text ``https://react.dev/learn`` and it will do its
best with the pieces: it searches for the words *https react dev learn* and
returns whatever ranks highest for them. In the audit's own captured
3,000-query run, every one of the 130 URL-anchored queries came back with
an unrelated Wikipedia article — ``https://docs.python.org/3/`` was answered
with the article *HTTPS*, and ``https://en.wikipedia.org/wiki/Brahmaputra_River``
with the article *Ganges*.

The user named the page. The page is the answer.

This module recognises such a query and hands the caller the URL to read
together with the request that qualifies it ("main article text without
navigation chrome"). The routing itself lives in the facade
(:mod:`iralens.core`), which is the layer that knows both how to search and
how to read.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

#: A URL at the very head of a query.
_LEADING_URL_RE = re.compile(r"^(https?://[^\s<>\"'`]+)", re.IGNORECASE)

#: Punctuation that ends a sentence, not a URL.
_TRAILING_PUNCTUATION = ".,;:!?)]}\"'"

#: Another URL inside the rest of the query.
_ANY_URL_RE = re.compile(r"https?://", re.IGNORECASE)

#: Words that start a question rather than a request about a page.
_QUESTION_WORDS = frozenset("""
    what why how when where who whom whose which
    is are was were do does did can could should would will has have had
    explain tell meaning difference
""".split())


def _looks_like_a_question(text: str) -> bool:
    """Is this text a question about something, rather than about a page?

    "https://react.dev/learn main article text without navigation chrome"
    asks for a part of the page. "https://stackoverflow.com/questions/1 how
    do I reverse a list?" asks a question that the page may or may not
    answer, and the search engines are better at that.
    """
    words = (text or "").split()
    if not words:
        return False
    if text.rstrip().endswith("?"):
        return True
    return words[0].lower().strip(".,:;!?") in _QUESTION_WORDS


def _split_url(query: str) -> Tuple[str, str]:
    """``(url, rest)`` for a query that starts with a URL.

    The URL ends at the first space; trailing sentence punctuation is not
    part of it. The rest is what the query asks for *from* the page.
    """
    raw = (query or "").strip()
    match = _LEADING_URL_RE.match(raw)
    if not match:
        return "", ""
    matched = match.group(1)
    url = matched
    while url and url[-1] in _TRAILING_PUNCTUATION:
        url = url[:-1]
    return url, raw[len(matched):].strip()


def leading_url(query: str) -> str:
    """The URL a query starts with, or ``""`` when it starts with anything else.

    A query that merely *mentions* a URL somewhere in the middle is not a
    read request.
    """
    return _split_url(query)[0]


def read_request(query: str) -> Optional[Tuple[str, str]]:
    """The ``(url, request)`` pair a query asks to read, or ``None``.

    A query is a read request when it names a URL and the rest of it is
    what the caller wants *from* that page. A query that puts a second URL
    in the rest is asking about two pages at once ("compare A and B"), which
    a single read cannot answer, so it stays a search.
    """
    url, rest = _split_url(query)
    if not url:
        return None
    if _ANY_URL_RE.search(rest):
        return None
    if _looks_like_a_question(rest):
        return None
    return url, rest


def read_response(
    query: str,
    url: str,
    artifact: Any,
    *,
    request: str = "",
    max_chars: int = 4000,
) -> Any:
    """A search-shaped response carrying the page the query named.

    The page is presented as the one and only result: the search results for
    the *text* of a URL are noise (searching "https react dev learn" is what
    produced the Wikipedia articles this module exists to stop). The page
    text is returned as the result's text, so a caller that asked for "the
    main article text" gets the article.
    """
    from .intent import classify_query
    from .ranking import authority, combine, freshness, relevance, today_utc
    from .schema import EngineOutcome, RankedResult, SearchResponse
    from .urls import canonical_url
    from ..settings import Settings

    settings = Settings()
    title = (getattr(artifact, "title", "") or url).strip()
    content = (getattr(artifact, "content", "") or "").strip()
    text = content[:max_chars] if content else ""
    intent = classify_query(query)

    rel = relevance(1, title, text, query, url=url, answer_type=intent.answer_type)
    factors = {
        "relevance": round(min(1.0, rel), 4),
        "authority": authority(url, settings.authority_weights, settings.default_authority),
        "freshness": freshness(None, today_utc(), settings.freshness_half_life_days,
                               settings.neutral_freshness),
        # The page was asked for by name: the one source that answers is
        # the whole agreement there is.
        "agreement": 1.0,
    }
    weights = {"relevance": settings.weight_relevance,
               "authority": settings.weight_authority,
               "freshness": settings.weight_freshness,
               "agreement": settings.weight_agreement}
    result = RankedResult(
        title=title,
        url=url,
        canonical_url=canonical_url(url) or url,
        snippet=text,
        score=combine(factors, weights),
        score_breakdown=factors,
        engines=["read"],
        engine_ranks={"read": 1},
        published_at=None,
        date_status="unknown",
        queries=[query],
        security_flags=list(getattr(artifact, "security_flags", []) or []),
        merged_from=[],
        date_evidence="",
    )
    outcome = EngineOutcome(
        engine="read",
        status="results",
        kind="ok",
        count=1,
        detail=(f"read the page named in the query ({request})" if request
                else "read the page named in the query"),
    )
    summary = f"read the page at {url} instead of searching for its text"
    response = SearchResponse(
        query=query,
        results=[result],
        queries=[{"text": query, "strategy": "read"}],
        outcomes=[outcome],
        fallbacks=[],
        filters={},
        cache={"status": "bypass", "stored": False, "fresh": True},
        dedup_log=[],
        no_results_reason="",
        security_flags=list(getattr(artifact, "security_flags", []) or []),
        summary=summary,
    )
    return response


__all__ = ["leading_url", "read_request", "read_response"]
