# -*- coding: utf-8 -*-
"""Engine fallback chain.

For one query, engines are tried in order until `min_engines` of them return
results (for cross-engine agreement) or the chain runs out. Each failed or
empty engine is recorded with a classified reason and a `fallbacks` entry, so
the caller can tell "engine blocked" apart from "query has no results".

Retries apply only to transient failures. Captcha and rate-limit signals are
not retried, because retrying them makes the block worse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

from ..reliability import (
    KIND_EMPTY,
    KIND_OK,
    CircuitBreaker,
    RetryPolicy,
    call_with_retries,
    failure_kind,
    is_transient,
)
from typing import Optional

from .engines.base import SearchBackend
from .schema import EngineOutcome, SearchFilters, SearchHit


KIND_CIRCUIT_OPEN = "circuit_open"


@dataclass
class ChainResult:
    hits: List[SearchHit] = field(default_factory=list)
    outcomes: List[EngineOutcome] = field(default_factory=list)
    fallbacks: List[Dict[str, str]] = field(default_factory=list)
    succeeded: List[str] = field(default_factory=list)


def run_chain(
    query: str,
    filters: SearchFilters,
    limit: int,
    chain: List[str],
    backends: Mapping[str, SearchBackend],
    context: Any,
    *,
    min_engines: int,
    policy: RetryPolicy,
    breaker: Optional[CircuitBreaker] = None,
    sleep=None,
) -> ChainResult:
    result = ChainResult()
    pending = list(chain)
    while pending and len(result.succeeded) < max(1, min_engines):
        name = pending.pop(0)
        backend = backends[name]
        modes = backend.filter_modes(filters)
        if breaker is not None and not breaker.allow(name):
            result.outcomes.append(EngineOutcome(
                engine=name, status="skipped", kind=KIND_CIRCUIT_OPEN, filter_modes=modes,
                detail="engine skipped after repeated failures; retrying after cooldown",
            ))
            _record_switch(result, name, pending, KIND_CIRCUIT_OPEN)
            continue
        try:
            hits, attempts = call_with_retries(
                lambda b=backend: b.search(backend.build_query(query, filters), filters, limit, context),
                policy,
                should_retry=is_transient,
                **({"sleep": sleep} if sleep else {}),
            )
        except Exception as exc:  # classified below; the chain must keep going
            kind = failure_kind(exc)
            if breaker is not None:
                breaker.record_failure(name)
            detail = str(getattr(exc, "detail", "") or getattr(exc, "message", "") or exc)[:300]
            result.outcomes.append(EngineOutcome(
                engine=name, status="failed", kind=kind, detail=detail,
                filter_modes=modes,
                attempts=getattr(policy, "max_attempts", 1) if kind == "transient" else 1,
            ))
            _record_switch(result, name, pending, kind)
            continue

        if breaker is not None:
            breaker.record_success(name)   # an empty page still means the engine responded
        if not hits:
            result.outcomes.append(EngineOutcome(
                engine=name, status="empty", kind=KIND_EMPTY, filter_modes=modes, attempts=attempts,
            ))
            _record_switch(result, name, pending, KIND_EMPTY)
            continue

        result.outcomes.append(EngineOutcome(
            engine=name, status="results", kind=KIND_OK, count=len(hits),
            filter_modes=modes, attempts=attempts,
        ))
        result.hits.extend(hits)
        result.succeeded.append(name)
    return result


def _record_switch(result: ChainResult, name: str, pending: List[str], reason: str) -> None:
    if pending:
        result.fallbacks.append({"from": name, "to": pending[0], "reason": reason})
