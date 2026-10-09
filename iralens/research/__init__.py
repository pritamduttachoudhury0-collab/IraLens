# -*- coding: utf-8 -*-
"""Research intelligence (Phase 3): planner, evidence, contradictions,
synthesis, and provenance. Deterministic by default; LLM adapters are optional.
"""

from .planner import DeterministicExpander, ResearchPlanner
from .schema import ResearchOptions, ResearchReport

__all__ = ["ResearchPlanner", "DeterministicExpander", "ResearchOptions", "ResearchReport"]
