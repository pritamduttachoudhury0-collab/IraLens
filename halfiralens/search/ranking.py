# -*- coding: utf-8 -*-
"""Explainable ranking for deduplicated search results.

Score = weighted mean of four factors, each in [0, 1]:
  relevance  - best engine rank (1/rank) blended with query-term overlap
  authority  - domain-suffix weight from Settings.authority_weights
  freshness  - exponential decay by age when a date is known, else neutral
  agreement  - fraction of queried engines that returned the page

Every factor value is returned in `score_breakdown`, so a ranking can be
explained and audited. Weights come from Settings and are normalized to sum 1.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Dict, Mapping, Optional, Tuple

from ..search.schema import SearchHit
from .urls import domain_of

_TERM_RE = re.compile(r"\w+", re.UNICODE)


def _terms(text: str) -> set:
    return {t.lower() for t in _TERM_RE.findall(text or "") if len(t) > 2}


def relevance(best_rank: int, title: str, snippet: str, question: str) -> float:
    rank_part = 1.0 / max(1, best_rank)
    q_terms = _terms(question)
    overlap = len(q_terms & _terms(f"{title} {snippet}")) / len(q_terms) if q_terms else 0.0
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
