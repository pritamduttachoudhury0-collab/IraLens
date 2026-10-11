# BENCHMARKS.md — the reliability loop

Every full run of `benchmarks/run_benchmark.py` gets one row here: the metrics,
the commit, the date and the kind of network it ran on. A row is appended by the
runner **only when every threshold in `benchmarks/thresholds.json` passes**;
failed or incomplete runs are recorded below the table with what failed instead
of being quietly dropped.

The thresholds (frozen, see `benchmarks/thresholds.json`):

| # | threshold | requirement |
|---|---|---|
| 1 | Availability | ≥ 99% of queries return 1+ real results (not empty, not captcha) |
| 2 | Quality | top-5 acceptable for ≥ 85% of gold queries; held-out within 5 points of dev |
| 3 | Redundancy | ≥ 2 independent engines each succeed 80%+ on their own |
| 4 | Latency | search p50 ≤ 3s, p95 ≤ 10s; read p95 ≤ 8s |
| 5 | Load | 10 queries/min for 30 min at 5 concurrent, < 2% failures |
| 6 | Extraction | ≥ 90% of `urls.json` yield correct main text; JS-heavy pages fall back to the browser |
| 7 | Honesty | zero silent failures; typed errors; D-079 exit codes; SSRF + injection suites green |

All seven must hold on **3 consecutive full runs** for the benchmark to pass.

## Runs

| date | commit | network | availability | quality dev | quality held-out | engines ≥80% | search p50 | search p95 | read p95 | load failure | extraction | honesty | verdict | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
<!-- benchmark-rows -->

### Run log

**2026-10-11 — UNVERIFIED from the build sandbox (not a benchmark row).**
The development sandbox has an allowlisted egress policy (only `github.com`,
`api.github.com`, `codeload.github.com`, `registry.npmjs.org`, `pypi.org`,
`files.pythonhosted.org` are reachable; everything else — including
`example.com` — fails at the TCP/TLS layer, verified with `curl` and with the
runner's own preflight probe). Search engines and general web pages are
therefore unreachable from here, so thresholds 1–6 **cannot be measured in this
environment** and no row is claimed. What was run and what it showed:

* `python benchmarks/run_benchmark.py --self-test` — **PASSED**. Validates the
  harness (metric math, threshold evaluation, table, `results.json` round-trip)
  against a synthetic system at known pass/fail rates. Not a measurement.
* `python benchmarks/run_benchmark.py --all --skip-load --limit 3
  --network datacenter` — **FAIL, correctly**: availability 0.00, quality 0.00,
  extraction 0.00, all queries `engines_failed` (`page_unavailable` /
  DNS failures), every URL `page_unavailable`, no silent failures, honesty
  10/11 CLI checks green at that time (the 11th was a harness bug, fixed).
  This is what a blocked network looks like; the runner refused to report it as
  anything else and appended no row.
* Same command after the D-080…D-084 fixes, `--limit 5`: **9.6 s wall** instead
  of 12 minutes for a comparable smoke run (the dead-engine hang is gone),
  honesty **12/12**, and still a correctly reported FAIL on availability,
  quality and extraction. Latency verdicts from a blocked network are
  meaningless (everything fails fast) and are not claimed as passes.
* The offline test suite, `ruff`, and `mypy` — see the session report.

### Ideas for the next loop iteration (not implemented here)

Ordered by expected effect on the thresholds, all measurable by this benchmark:

1. **Per-engine health scoring across processes.** The circuit breaker is
   in-memory, so a CLI agent that starts a new process per query re-tries a
   dead engine every time. Persisting a bounded per-engine score (successes,
   last failure, EWMA latency) and ordering the chain by it — never dropping an
   engine — would cut p95 latency and raise availability without changing the
   result contract.
2. **Adaptive pacing per engine.** A minimum interval per engine host, relaxed
   when responses are fast and tightened after a `rate_limited`/`captcha`,
   protects the load threshold (5 concurrent for 30 minutes) on shared IPs.
3. **More independent engines.** The redundancy threshold needs 2+ engines at
   80% solo: a self-hosted SearXNG (`deploy/searxng/`) plus DuckDuckGo is the
   cheapest start; adding a third, genuinely independent index would make the
   metric robust to one engine changing its layout. Verify each candidate's
   terms of use before adding it.
4. **Layout-change canaries.** A scheduled fixture refresh (fetch each engine's
   SERP weekly, diff the parser's hit count) turns "layout changed" from a
   field report into a monitored signal.
5. **Ranking/dedup work** only after a live run shows quality < 85%.

**What must be run on an open network** (exact command):

```bash
.venv/bin/python benchmarks/run_benchmark.py --all --with-load --network home
```

…three times in a row; each run appends its row above when it passes. The
`--network` label is yours to set honestly (`home`, `datacenter`, `proxy`).
