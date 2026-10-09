# -*- coding: utf-8 -*-
"""Deterministic synthesis: cluster claims by subject, then state each cluster once.

A statement cites every claim in its cluster. Its status is:
  contested       - a claim in the cluster has an open contradiction candidate
  corroborated    - two or more distinct domains state it
  single_source   - only one domain states it

Confidence is a transparent formula (no hidden model):
  base + per_domain * (domains - 1), times the mean source score, minus a
  contested penalty, clamped to [0, 1]. Constants come from Settings.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ..settings import Settings
from .evidence import jaccard
from .schema import Claim, Contradiction, EvidenceSource, Statement


def cluster(claims: Sequence[Claim], threshold: float) -> List[List[Claim]]:
    clusters: List[List[Claim]] = []
    for claim in claims:
        for members in clusters:
            if jaccard(members[0].subject_terms, claim.subject_terms) >= threshold:
                members.append(claim)
                break
        else:
            clusters.append([claim])
    return clusters


def make_statement(
    members: Sequence[Claim],
    contradictions: Sequence[Contradiction],
    sources: Dict[str, EvidenceSource],
    settings: Settings,
    text: Optional[str] = None,
) -> Statement:
    """Score one cluster of claims. `text` overrides the default (best claim) wording."""
    contested_ids = {cid for c in contradictions for cid in c.claim_ids}
    source_ids = list(dict.fromkeys(c.source_id for c in members))
    domains = {sources[s].domain for s in source_ids if s in sources}
    contested = any(c.id in contested_ids for c in members)
    if contested:
        status = "contested"
    elif len(domains) >= 2:
        status = "corroborated"
    else:
        status = "single_source"
    quality = [sources[s].score for s in source_ids if s in sources]
    mean_quality = sum(quality) / len(quality) if quality else 0.0
    conf = settings.confidence_base + settings.confidence_per_domain * max(0, len(domains) - 1)
    conf = conf * mean_quality
    if contested:
        conf -= settings.contested_penalty
    if text is None:
        best = max(members, key=lambda c: (sources[c.source_id].score if c.source_id in sources else 0.0,
                                           -len(c.text)))
        text = best.text
    return Statement(
        id="",
        text=text,
        status=status,
        confidence=round(max(0.0, min(1.0, conf)), 3),
        claim_ids=[c.id for c in members],
        source_ids=source_ids,
        distinct_domains=len(domains),
    )


def build_statements(
    claims: Sequence[Claim],
    contradictions: Sequence[Contradiction],
    sources: Dict[str, EvidenceSource],
    settings: Settings,
) -> List[Statement]:
    statements = [
        make_statement(members, contradictions, sources, settings)
        for members in cluster(claims, settings.claim_subject_similarity)
    ]
    statements.sort(key=lambda s: (-s.confidence, s.text))
    for i, st in enumerate(statements, start=1):
        st.id = f"st{i}"
    return statements
