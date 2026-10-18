# -*- coding: utf-8 -*-
"""Explainable ranking for deduplicated search results.

Score = weighted mean of four factors, each in [0, 1]:
  relevance  - best engine rank (1/rank) blended with term overlap and,
               when the query contains quoted phrases, phrase coverage.
               Terms are tokenized symbol-aware (`c++`, `c#`, `.net`), so a
               C++ query only overlaps pages that actually mention C++.
  authority  - domain-suffix weight from Settings.authority_weights
  freshness  - exponential decay by age when a date is known, else neutral
  agreement  - fraction of queried engines that returned the page

Every factor value is returned in `score_breakdown`, so a ranking can be
explained and audited. Weights come from Settings and are normalized to sum 1.

The engine layer applies one further adjustment before combining: groups whose
hits carry a `prompt_injection:*` security flag get their relevance factor
halved (see SearchEngine._rank). Flagged content is still returned — never
silently dropped — but it ranks below clean content.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from datetime import date, datetime, timezone
from typing import Dict, Mapping, Optional, Tuple

from .querytext import quoted_phrases, term_set
from .urls import domain_of


_HUB_TITLE_PREFIXES = ("list of ", "category:", "portal:", "index of ")
_DICTIONARY_HOSTS = ("merriam-webster.com", "dictionary.cambridge.org",
                     "dictionary.com", "thefreedictionary.com", "lexico.com")


def _specificity_multiplier(url: str, title: str, question: str,
                            answer_type: str = "") -> float:
    """Penalise hub/homepage/dictionary shapes that match words, not answers.

    Benchmark defect D1/D5: a hub whose *title* contains query words outranked
    the specific document. A homepage, a ``List of``/category page, or a
    dictionary entry is the right answer only for a definitional query.
    """
    import urllib.parse
    host = ""
    path = ""
    if url:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        path = parts.path or ""
    mult = 1.0
    q_terms = term_set(question)
    wants_list = "list" in q_terms
    title_low = (title or "").strip().lower()
    if title_low.startswith(_HUB_TITLE_PREFIXES) and not wants_list:
        mult *= 0.65
    if "disambiguation" in title_low or "disambiguation" in path.lower():
        mult *= 0.6
    segments = [s for s in path.split("/") if s]
    if url and len(segments) == 0 and len(q_terms) >= 3:
        mult *= 0.8  # bare homepage for a multi-term, specific query
    definitional = answer_type in ("definition", "entity_lookup") or \
        (question or "").strip().lower().startswith(
            ("what does", "meaning of", "define ", "definition of"))
    if host.endswith("wikipedia.org") and answer_type in (
            "procedure", "statistic", "current_event", "comparison", "error"):
        mult *= 0.65
    if host in _DICTIONARY_HOSTS or host.endswith(".dictionary.cambridge.org"):
        if not definitional:
            mult *= 0.5
    return mult



#: What a request shape asks for, and what such a source looks like.
#:
#: A question's request shape says which *kind* of source would answer it:
#: the official documentation, the primary source, the government record, the
#: latest release. A page of the right kind is a better answer than a
#: better-ranking page of the wrong kind, and the shape is the only place
#: that distinction is available — the query text alone cannot say it.
_SOURCE_KINDS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "documentation": {
        "hosts": (".readthedocs.io", ".readthedocs.org", ".readthedocs.com"),
        "url": ("/docs/", "/doc/", "/documentation", "/reference/", "/manual/",
                "/developer/", "/api/", "/tutorial", "/guide", "/how-to",
                "/cookbook", "docs.", "developer."),
        "title": ("documentation", "docs", "reference", "manual", "guide",
                  "tutorial", "cookbook", "getting started"),
    },
    "official": {
        "hosts": (".gov", ".gov.in", ".gov.uk", ".mil", ".nic.in", ".state.gov",
                  ".europa.eu", ".un.org", ".who.int", ".int"),
        "url": ("/guideline", "/circular", "/notification", "/act", "/rules",
                "/official"),
        "title": ("official", "government", "ministry", "department", "guideline",
                  "circular", "notification", "statute", "policy", "act"),
    },
    "primary": {
        "hosts": (".edu", ".ac.uk", ".ac.in", ".edu.in", "doi.org", "arxiv.org",
                  "jstor.org", "pubmed.ncbi.nlm.nih.gov", "springer.com",
                  "sciencedirect.com", "ieee.org", "acm.org", "nature.com",
                  "science.org", "plos.org", "biomedcentral.com"),
        "url": ("/doi/", "/abs/", "/pdf", "doi.org"),
        "title": ("original paper", "primary source", "primary study", "research",
                  "study", "journal", "preprint", "doi", "peer reviewed",
                  "systematic review"),
    },
    "latest": {
        "hosts": (),
        "url": ("/releases", "/release", "/changelog", "/news", "/blog",
                "/whatsnew", "/what-s-new", "/versions", "/downloads", "/news/"),
        "title": ("release", "changelog", "what's new", "whats new", "new in",
                  "version", "latest"),
    },
    "dataset": {
        "hosts": ("data.gov", "data.gov.in", "worldbank.org", "un.org",
                  "europa.eu", "oecd.org", "kaggle.com", "datahub.io",
                  "data.gov.uk", "nseindia.com"),
        "url": (".csv", ".json", "/api/", "dataset", "/data/", "indicator/"),
        "title": ("dataset", "data", "statistics", "download", "dashboard",
                  "indicator", "metadata"),
    },
    "reference": {
        "hosts": (".edu", ".ac.uk", ".ac.in", ".edu.in",
                  "scholar.google.com", "semanticscholar.org",
                  "researchgate.net", "arxiv.org", "jstor.org"),
        "url": ("/lecture", "/notes", "/course", "/syllabus", "/pdf", "/paper"),
        "title": ("lecture notes", "notes", "course", "syllabus", "references",
                  "bibliography", "textbook", "academic"),
    },
    "encyclopedia": {
        "hosts": ("wikipedia.org", "britannica.com", "encyclopedia.com"),
        "url": (),
        "title": ("wikipedia", "encyclopedia", "britannica"),
    },
}

#: Which request words ask for which kind of source.
_KIND_WORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("documentation", ("documentation", "docs", "manual", "reference page")),
    ("primary", ("primary", "original", "doi", "arxiv", "peer reviewed",
                 "peer-reviewed", "systematic review", "study", "studies",
                 "literature", "paper")),
    ("official", ("government", "official", "statute", "guideline", "legal",
                  "legislation", "policy", "conservation", "archive",
                  "announcement", "record", "procedure")),
    ("latest", ("latest", "current", "release", "recent", "newest", "update",
                "changes")),
    ("dataset", ("dataset", "data", "csv", "api", "dashboard", "statistics",
                 "download", "metadata")),
    ("reference", ("lecture", "notes", "academic", "references", "bibliography",
                   "textbook", "course", "university", "pdf")),
    ("encyclopedia", ("encyclopedia", "fact check", "wikipedia")),
)

#: How much a page of the requested kind is preferred.
#:
#: The preference is *additive and bounded* rather than multiplicative, so
#: that the kind of a page can never outweigh how well the page matches the
#: question: a page of the right kind that barely mentions the subject
#: cannot outrank a page of the wrong kind that answers it outright. The
#: bonus is worth at most a fifth of the relevance scale.
#:
#: Calibrated against the audit's own captured 3,000-query result set by
#: re-ranking it with and without the preference
#: (`benchmarks/measure_shape_ranking.py --sweep`):
#:
#:     bonus   top-1 changed   better   worse
#:     0.05             3            0       0
#:     0.10            18            3       2
#:     0.15            27            6       3
#:     0.20            38            7       3
#:     0.30            76           18       3
#:     0.50           179           65      12
#:
#: 0.20 is a fifth of the relevance scale: enough to settle a near-tie in
#: favour of the kind of source the question asked for, and not enough to
#: let the kind of a page outrank a page that simply answers the question.
#: Note that the coverage metric looks at the *best* result of a set, not
#: the first, so this preference moves which answer is presented rather
#: than the coverage figure.
_REQUEST_KIND_BONUS = 0.20


@lru_cache(maxsize=4096)
def request_kinds(question: str) -> frozenset:
    """The kinds of source this question asks for.

    Derived from the question's *request shape* — the part that says which
    kind of source would answer it, as opposed to the part that says what
    the question is about. An empty set means the question does not ask for
    a particular kind of source, and no preference is applied.
    """
    from .queryfocus import split_subject

    _subject, shape = split_subject(question or "")
    if not shape:
        # The whole question may itself be a request ("official dataset
        # download" names no subject at all); then the shape is the query.
        shape = question or ""
        if not _starts_with_request_word(question or ""):
            return frozenset()
    words = set(shape.lower().replace(",", " ").replace("-", " ").split())
    kinds = set()
    for kind, triggers in _KIND_WORDS:
        if words & set(triggers):
            kinds.add(kind)
    return frozenset(kinds)


def _starts_with_request_word(question: str) -> bool:
    """Does the question begin with a request word ("official dataset...")?"""
    from .queryfocus import REQUEST_MARKERS, _first_content_word, _words_with_case

    if not question:
        return False
    return _first_content_word(_words_with_case(question)) in REQUEST_MARKERS


def _has_word(text: str, marker: str) -> bool:
    """Is this marker present as a whole word in the text?

    Plain substring matching would let "database" match the dataset marker
    "data" and "policies" match the official marker "policy".
    """
    if " " in marker:
        return marker in text
    return re.search(rf"\b{re.escape(marker)}\b", text) is not None


def _request_kind_bonus(url: str, title: str, question: str) -> float:
    """The relevance bonus for a page of the kind the question asked for."""
    kinds = request_kinds(question)
    if not kinds:
        return 0.0
    import urllib.parse
    host = ""
    path = ""
    if url:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        path = (parts.path or "").lower()
    title_low = (title or "").lower()
    url_low = f"{host}{path}".lower()
    if not [segment for segment in path.split("/") if segment]:
        # A bare homepage is the entry point to the right kind of source,
        # not the source itself. The specificity penalty already says so;
        # the shape preference must not undo it.
        return 0.0
    for kind in kinds:
        spec = _SOURCE_KINDS.get(kind)
        if not spec:
            continue
        if any(host.endswith(suffix) for suffix in spec["hosts"]):
            return _REQUEST_KIND_BONUS
        if any(marker in url_low for marker in spec["url"]):
            return _REQUEST_KIND_BONUS
        if any(_has_word(title_low, marker) for marker in spec["title"]):
            return _REQUEST_KIND_BONUS
    return 0.0

def relevance(best_rank: int, title: str, snippet: str, question: str, *,
              url: str = "", answer_type: str = "") -> float:
    """Term/phrase coverage blended with the engine's own rank.

    Overlap is no longer title+snippet pooled (D1): when a snippet exists it
    carries the answer-density signal (60%) against the title (40%); with no
    snippet the title alone is used, preserving the classic values. The result
    is then adjusted by a URL/title *specificity* multiplier (hub, homepage,
    dictionary shapes demoted for non-definitional queries).

    Without quoted phrases: 0.5 * (1/rank) + 0.5 * overlap. With quoted
    phrases: 0.5 * (1/rank) + 0.35 * overlap + 0.15 * phrase_coverage.
    """
    rank_part = 1.0 / max(1, best_rank)
    q_terms = term_set(question)
    if q_terms:
        title_overlap = len(q_terms & term_set(title or "")) / len(q_terms)
        if (snippet or "").strip():
            snippet_overlap = len(q_terms & term_set(snippet)) / len(q_terms)
            overlap = 0.4 * title_overlap + 0.6 * snippet_overlap
        else:
            overlap = title_overlap
    else:
        overlap = 0.0
    phrases = quoted_phrases(question)
    if phrases:
        haystack = f"{title} {snippet}".casefold()
        covered = sum(1 for p in phrases if p.casefold() in haystack)
        phrase_part = covered / len(phrases)
        base = 0.5 * rank_part + 0.35 * overlap + 0.15 * phrase_part
    else:
        base = 0.5 * rank_part + 0.5 * overlap
    # The shape preference and the specificity penalty are independent: the
    # kind of a page is worth a bounded bonus, and the homepage penalty is
    # exactly what says "this is a hub, not the answer".
    base = min(1.0, base + _request_kind_bonus(url, title, question))
    return round(min(1.0, base
                      * _specificity_multiplier(url, title, question, answer_type)), 4)


def authority(url: str, weights: Mapping[str, float], default: float) -> float:
    host = domain_of(url)
    best: Optional[Tuple[int, float]] = None
    for suffix, value in weights.items():
        s = suffix.lower().lstrip(".")
        if host == s or host.endswith("." + s):
            if best is None or len(s) > best[0]:
                best = (len(s), float(value))
    return round(best[1] if best else default, 4)


def freshness(published_at: Optional[str], now: date, half_life_days: float, neutral: float) -> float:
    if not published_at:
        return neutral
    try:
        when = date.fromisoformat(published_at[:10])
    except ValueError:
        return neutral
    age = max(0, (now - when).days)
    return round(math.pow(0.5, age / max(1e-6, half_life_days)), 4)


def combine(factors: Dict[str, float], weights: Dict[str, float]) -> float:
    total = sum(weights.values()) or 1.0
    return round(sum(factors[k] * weights.get(k, 0.0) for k in factors) / total, 4)


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
