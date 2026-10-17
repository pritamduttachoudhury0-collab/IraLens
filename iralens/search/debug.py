# -*- coding: utf-8 -*-
"""Ranking debug dump (Phase 0 instrumentation, no behaviour change).

For a question and a list of SearchHit objects, show per result: the four
ranking factors as computed by the *current* ranking code, the supplying
engine, its best rank, and whether it carries security flags. Used to
reproduce benchmark failures on demand (docs/improvements/phase-0.md).
"""
from __future__ import annotations

from typing import Any, Dict, List

from .ranking import authority, freshness, relevance, today_utc
from .schema import SearchHit


def debug_dump(question: str, hits: List[SearchHit], *, authority_weights=None,
               default_authority: float = 0.5, half_life_days: float = 365.0,
               neutral_freshness: float = 0.5) -> List[Dict[str, Any]]:
    weights = authority_weights or {".gov": 0.9, ".edu": 0.85, ".org": 0.6}
    today = today_utc()
    out: List[Dict[str, Any]] = []
    for h in hits:
        out.append({
            "title": h.title, "url": h.url, "engine": h.engine, "best_rank": h.position,
            "flagged": bool(h.security_flags), "security_flags": list(h.security_flags),
            "factors": {
                "relevance": relevance(h.position, h.title, h.snippet, question, url=h.url),
                "authority": authority(h.url, weights, default_authority),
                "freshness": freshness(h.published_at, today, half_life_days, neutral_freshness),
            },
            "published_at": h.published_at,
        })
    return out
