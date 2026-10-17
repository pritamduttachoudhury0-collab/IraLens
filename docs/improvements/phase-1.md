# Phase 1 — Ranking and answer typing (D1, D5)

## Changes

- `ranking.relevance()` now separates title and snippet coverage (snippet weighted
  more when available), accepts URL/intent context, and demotes hub/disambiguation,
  homepage, dictionary, and Wikipedia results for non-definitional intents.
- Intent-aware rank boosts prioritize jurisdiction-specific official domains and
  source types (procedure → government/statute, error → docs/Q&A, statistic → official
  data). Default authority weights now recognize `.gov.in`, `.nic.in`, `.ac.in`.
- Wikipedia remains usable for definitions/entity lookups; for procedures, statistics,
  current events, comparisons and errors the final list caps Wikipedia to one result.
  It is not silently padded with more hubs if fewer than `max_results` remain.
- `search/debug.py::debug_dump()` exposes current factors, engine, rank and flags.

## Regression and diagnostics

- `tests/regression/test_benchmark_failures.py` covers a case where legacy scoring
  gives the generic category page 0.800 vs the specific official result 0.625; new
  scoring orders the specific page above the hub. Dictionary distractor and actual
  engine-level Wikipedia-cap tests also pass.
- Offline score comparison for those two hand-constructed fixtures (not search
  quality): legacy formula and new formula are in the test history; the regression
  assertion tests the ordering, not private score constants.
- Holdout structural check: `.venv/bin/python benchmark/holdout/evaluate_offline.py`
  reports 300/300 queries with non-empty token sets; 30 in each category. This is
  not P@5. Live holdout P@5, per-category quality, Wikipedia share and human relevance
  are **NOT MEASURED** (raw benchmark and live web egress unavailable here).

## Reproduction limit

The benchmark prompt's `benchmark/raw/` directory is absent from this checkout. The
specific R5-Q283/Q297/Q267 rows could not be opened/replayed; reproduction fixtures
are based on the report's verbatim descriptions, and are labelled as such. The 1,300
benchmark queries were not used for tuning. Holdout was frozen first and not queried.
