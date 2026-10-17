# Improvement holdout — frozen before tuning

- `queries_holdout.jsonl` — 300 queries, 30 per category (same 10 categories
  as the 1,300-query benchmark), written for the improvement programme and
  frozen **before** any ranking/intent code was changed in that programme.
  - SHA-256: `2c96ebe8ba65b3a53278b7f9e4b411da08a972e8412ffb037ada595d28306aa5`
  - Builder: `build_holdout.py` (run once; do not regenerate over the frozen file).
- `extraction_pages.jsonl` — 100 pages with an expected answer span, for the
  first extraction measurement (Phase 4). SHA-256:
  `16d801a9a206b50f125cb00ba74a2e69133e7e4e64a53dbba7313e7a33832920`

Rules: do not tune against these queries; do not edit them after freezing;
report results with denominators. Live evaluation of this holdout requires a
normal network — in a restricted-egress environment it is NOT MEASURED and
must be reported as such (see docs/improvements/FINAL_REPORT.md).
