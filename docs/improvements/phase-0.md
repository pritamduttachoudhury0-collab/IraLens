# Phase 0 — Reproduce and instrument

Baseline (this machine, restricted egress, 2026-10-17):
- `bash scripts/setup.sh` → exit 0, `iralens doctor` OK, version 0.2.1.
- `.venv/bin/python -m pytest -q` → **442 passed, 14 skipped, 0 failed** (matches README).
- Debug dump: `iralens/search/debug.py::debug_dump(question, hits)` returns per-result
  relevance/authority/freshness factors, engine, best rank, flags — no weights changed.
- Holdout frozen before tuning: `benchmark/holdout/queries_holdout.jsonl`
  sha256 `2c96ebe8ba65b3a53278b7f9e4b411da08a972e8412ffb037ada595d28306aa5` (300 queries,
  30/category) + `extraction_pages.jsonl` (100 pages). No overlap with repo `benchmarks/queries.json`.
- Reproduction tests: `tests/regression/test_benchmark_failures.py` — deterministic
  fixtures for D1–D8, modelled on BENCHMARK_FINAL_REPORT §2/§53–54 (R5-Q283, Q297,
  Q267, Q228). D1's old formula is reconstructed from the checked-in baseline code
  and shown to put the generic page above the answer-specific page; the post-fix
  ordering assertion now passes. For other defects these are report-shaped fixtures,
  not replays of captured rows. NOTE: the benchmark's `benchmark/raw/` rows are not
  in this repo, so exact raw-row reproduction was impossible.
- Live re-run of 60 R5 queries: **NOT MEASURED** — this sandbox has no general web
  egress (GitHub/PyPI/npm only), so live engines cannot be reached. Recorded honestly
  per standing instruction 17.
