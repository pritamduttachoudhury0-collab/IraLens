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
    return round(min(1.0, base * _specificity_multiplier(url, title, question, answer_type)), 4)


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
