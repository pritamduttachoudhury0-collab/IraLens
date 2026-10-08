# -*- coding: utf-8 -*-
"""Every tunable limit, weight, threshold and TTL of the search, reliability,
and research layers — in one typed place.

Values come from the existing config system (`config.yaml` keys or
`HIL_<KEY>` environment variables) and fall back to the defaults below. No
other module hardcodes these numbers. Each default is justified in
DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from typing import Any, Dict, Tuple

DEFAULT_AUTHORITY: Dict[str, float] = {".gov": 0.9, ".edu": 0.85, ".org": 0.6}


@dataclass(frozen=True)
class Settings:
    # --- engines / fallback ------------------------------------------------
    search_engines: Tuple[str, ...] = ("semantic-search", "duckduckgo", "bing")
    search_min_engines: int = 2            # engines queried for cross-engine agreement
    search_max_results: int = 8
    search_timeout_seconds: int = 45       # per engine navigation/extraction budget
    # --- resource management -----------------------------------------------
    search_max_concurrent: int = 2         # simultaneous searches on the shared engine
    search_acquire_timeout_seconds: int = 60
    # --- ranking ------------------------------------------------------------
    weight_relevance: float = 0.4
    weight_authority: float = 0.2
    weight_freshness: float = 0.2
    weight_agreement: float = 0.2
    freshness_half_life_days: float = 365.0
    neutral_freshness: float = 0.5         # used when a result has no date
    authority_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_AUTHORITY))
    default_authority: float = 0.5
    # --- dedup ----------------------------------------------------------------
    dedup_title_similarity: float = 0.85
    # --- reformulation --------------------------------------------------------
    reformulate_max_queries: int = 3
    # --- reliability -----------------------------------------------------------
    retry_max_attempts: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 8.0
    # --- circuit breaker (per engine, per SearchEngine instance) ---------------
    breaker_failure_threshold: int = 3     # consecutive failures before an engine is skipped
    breaker_cooldown_seconds: float = 300.0
    # --- caching ---------------------------------------------------------------
    cache_enabled: bool = True
    cache_search_ttl_seconds: int = 900
    cache_page_ttl_seconds: int = 3600
    # --- research --------------------------------------------------------------
    research_max_rounds: int = 3
    research_max_queries: int = 12
    research_min_sources: int = 3
    research_coverage_threshold: float = 0.7

    @classmethod
    def from_config(cls, config: Any = None) -> "Settings":
        values: Dict[str, Any] = {}
        for f in fields(cls):
            default = f.default_factory() if f.default_factory is not MISSING else f.default
            raw = config.get(f.name, default) if config is not None else default
            values[f.name] = _coerce(raw, default)
        return cls(**values)


def _coerce(raw: Any, default: Any) -> Any:
    if isinstance(default, dict):
        if isinstance(raw, dict):
            return {str(k): float(v) for k, v in raw.items()}
        return dict(default)
    if isinstance(default, tuple):
        if isinstance(raw, str):
            return tuple(part.strip() for part in raw.split(",") if part.strip())
        return tuple(raw) if raw else default
    if isinstance(default, bool):
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw
