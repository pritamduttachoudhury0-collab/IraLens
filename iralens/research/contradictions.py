# -*- coding: utf-8 -*-
"""Heuristic contradiction candidates between claims from different sources.

Two claims are compared only when they come from different domains and their
subject terms overlap (Jaccard >= Settings.contradiction_similarity). Then:
  - numeric_mismatch: both state numbers and the number sets are disjoint
  - negation_mismatch: exactly one of them is negated

These are candidates, not verdicts. A "numeric mismatch" can be two different
measurements. Every contradiction is labeled heuristic, and the report says so.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

from .evidence import jaccard
from .schema import Claim, Contradiction


def detect(claims: Sequence[Claim], domains: Dict[str, str], threshold: float) -> List[Contradiction]:
    out: List[Contradiction] = []
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            if a.source_id == b.source_id:
                continue
            if domains.get(a.source_id) == domains.get(b.source_id):
                continue
            if jaccard(a.subject_terms, b.subject_terms) < threshold:
                continue
            kind = None
            explanation = ""
            if a.numbers and b.numbers and not (set(a.numbers) & set(b.numbers)):
                kind = "numeric_mismatch"
                explanation = f"'{a.text[:120]}' vs '{b.text[:120]}' give different numbers " \
                              f"({', '.join(a.numbers)} vs {', '.join(b.numbers)})"
            elif a.negated != b.negated:
                kind = "negation_mismatch"
                explanation = f"one claim is negated and the other is not: '{a.text[:120]}' vs '{b.text[:120]}'"
            if kind:
                out.append(Contradiction(
                    id=f"x{len(out) + 1}", kind=kind, claim_ids=[a.id, b.id],
                    subject_terms=sorted(set(a.subject_terms) & set(b.subject_terms)),
                    explanation=explanation,
                ))
    return out
