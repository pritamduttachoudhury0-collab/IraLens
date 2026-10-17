# Phase 2 — Engine resilience and honest degradation (D4, D6)

## Changes

- Existing SQLite persistent circuit breaker already implements a cooldown and
  single half-open probe lease (reviewed; no duplicate breaker was added).
- Every `SearchResponse` now serializes a `degradation` object identifying answered,
  failed and skipped engines and skip reasons, while retaining the existing
  `degraded` boolean and `outcomes` list.
- Short results now carry `shortfall_reason` (`capped`, `deduped`, `filtered`,
  `exhausted`) and add the reason/count to the human-readable summary. No padding.
- Added a Mojeek backend, HTML parser fixture and registry entry. It is **opt-in**
  and not in the default chain. It has not been live-verified or independently
  reviewed against current service terms from this restricted-egress environment;
  do not treat the parser as production-validated.

## Checks

- `tests/regression/test_benchmark_failures.py`: injected blocked first engine
  returns usable later-engine results and `degraded=true`; all-empty results remain
  typed `no_results`; short lists explicitly say `exhausted`; Wikipedia cap reports
  `capped`.
- `tests/test_reliability.py` covers existing persistent half-open behavior.
- `tests/test_mojeek.py`: 3 offline parser/registration tests. No live Mojeek claim.
- Degraded-mode p95, 30-minute soak, automatic recovery time, 5-result availability
  and network-layer blocked-engine behavior are **NOT MEASURED** in this sandbox.
