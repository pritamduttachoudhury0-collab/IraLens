# -*- coding: utf-8 -*-
"""Unified search subsystem (Phase 1 of IraLens).

Public entry points:
  - `SearchEngine`        orchestrator (fallback, filters, dedup, ranking, cache)
  - `SearchFilters`, `SearchOptions`, `SearchResponse`, `RankedResult` schemas
  - `to_artifacts`        legacy `Artifact` list conversion (IraLens.search)
"""

from .engine import SearchEngine, to_artifacts
from .schema import (FilterError, RankedResult, SearchFilters, SearchHit,
                     SearchOptions, SearchResponse)

__all__ = ["SearchEngine", "to_artifacts", "FilterError", "RankedResult",
           "SearchFilters", "SearchHit", "SearchOptions", "SearchResponse"]
