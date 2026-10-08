# -*- coding: utf-8 -*-
"""Pluggable reasoning interfaces for the research planner.

Two extension points, both with deterministic defaults:
  - Expander   proposes the next queries (default: research/planner.py)
  - Synthesizer turns claims into statements (default: research/synthesis.py)

An LLM can implement either one (see research/llm.py for a JSON-validating
adapter over any `complete(prompt) -> str` callable). Implementations must
never invent sources: every statement cites claim ids that exist, and the
planner validates that before the report is built.
"""

from __future__ import annotations

from typing import Dict, List, Protocol, Sequence

from .schema import Claim, Contradiction, EvidenceSource, Statement


class Expander(Protocol):
    def expand(self, question: str, round_no: int, used: Sequence[str],
               contested_terms: Sequence[Sequence[str]]) -> List[Dict[str, str]]:
        """Return [{'text': query, 'strategy': label}] for this round."""


class Synthesizer(Protocol):
    def synthesize(self, question: str, claims: Sequence[Claim], contradictions: Sequence[Contradiction],
                   sources: Dict[str, EvidenceSource]) -> List[Statement]:
        """Return statements whose claim_ids all exist in `claims`."""
