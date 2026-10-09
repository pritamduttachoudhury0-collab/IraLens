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


def relevance(best_rank: int, title: str, snippet: str, question: str) -> float:
    """Term/phrase coverage blended with the engine's own rank.

    Without quoted phrases: 0.5 * (1/rank) + 0.5 * term_overlap (the classic
    formula, now symbol-aware). With quoted phrases: 0.5 * (1/rank) +
    0.35 * term_overlap + 0.15 * phrase_coverage, so an "exact phrase" query
    rewards pages that actually contain the phrase.
    """
    rank_part = 1.0 / max(1, best_rank)
    q_terms = term_set(question)
    if q_terms:
        overlap = len(q_terms & term_set(f"{title} {snippet}")) / len(q_terms)
    else:
        overlap = 0.0
    phrases = quoted_phrases(question)
    if phrases:
        haystack = f"{title} {snippet}".casefold()
        covered = sum(1 for p in phrases if p.casefold() in haystack)
        phrase_part = covered / len(phrases)
        return round(0.5 * rank_part + 0.35 * overlap + 0.15 * phrase_part, 4)
    return round(0.5 * rank_part + 0.5 * overlap, 4)


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
