# BENCHMARKS.md — the reliability loop

Every full run of `benchmarks/run_benchmark.py` gets one row here: the
metrics, the commit, the date, and the kind of network it ran on. A row is
appended by the runner **only when every threshold in
`benchmarks/thresholds.json` passes**; failed or incomplete runs are recorded
below the table with what failed instead of being quietly dropped.

The thresholds (frozen, see `benchmarks/thresholds.json`):

| # | threshold | requirement |
|---|---|---|
| 1 | Availability | ≥ 99% of queries return 1+ real results (not empty, not captcha) |
| 2 | Quality | top-5 acceptable for ≥ 85% of gold queries; held-out within 5 points of dev |
| 3 | Redundancy | ≥ 2 independent engines each succeed 80%+ on their own |
| 4 | Latency | search p50 ≤ 3s, p95 ≤ 10s; read p95 ≤ 8s |
| 5 | Load | 10 queries/min for 30 min at 5 concurrent, < 2% failures |
| 6 | Extraction | ≥ 90% of `urls.json` yield correct main text; JS-heavy pages fall back to the browser |
| 7 | Honesty | zero silent failures; typed errors; documented exit codes; SSRF + injection suites green |

All seven must hold on **3 consecutive full runs** for the benchmark to pass.

## Runs

| date | commit | network | availability | quality dev | quality held-out | engines ≥80% | search p50 | search p95 | read p95 | load failure | extraction | honesty | verdict | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
<!-- benchmark-rows -->

### Run log

**2026-10-11 — harness validated; thresholds not measurable in a restricted
environment (not a benchmark row).** The verification environment available
at the time allowed outbound access only to package indexes and GitHub;
search engines and general web pages were unreachable (confirmed with the
runner's own preflight probe). What was run:

* `python benchmarks/run_benchmark.py --self-test` — **passed**. Validates
  the harness (metric math, threshold evaluation, table, `results.json`
  round-trip) against a synthetic system at known pass/fail rates. Not a
  measurement.
* `python benchmarks/run_benchmark.py --all --skip-load --limit 3 --network
  datacenter` — **failed, correctly**: availability 0.00, quality 0.00,
  extraction 0.00, every query `engines_failed`, no silent failures. That is
  what a blocked network looks like; the runner refused to report it as
  anything else and appended no row. Latency verdicts from a blocked network
  are meaningless (everything fails fast) and are not claimed.
* A later 5-query smoke run on the same network finished in 9.6 s wall
  (previously 12 minutes) after the dead-engine-hang fix, with honesty
  checks 12/12 — still a correctly reported failure on every measurable
  threshold.

**2026-10-16 — independent open-network audit (not a frozen-benchmark run).**
An unrestricted-network audit exercised a 100-query quality slice and a
60-page reading set against thresholds *derived from* this benchmark; see
`docs/audits/paid-search-replacement-audit-2026-10-14.md` for the full
table. The frozen 200-query/100-URL runner itself still awaits three
consecutive full passes on an open, policy-approved network — no row is
claimed until then.

**What an acceptance run takes** (exact command, three times in a row; each
passing run appends its row above):

```bash
.venv/bin/python benchmarks/run_benchmark.py --all --with-load --network home
```

The `--network` label is yours to set honestly (`home`, `datacenter`,
`proxy`).

### Standing notes for future loop iterations

1. **Measure the fixed engine order before learning one.** Persistent circuit
   state exists; routing does not yet order healthy sources by a learned
   score. Measure first on an approved network.
2. **Adaptive pacing is partial.** Wikimedia and the optional reference API
   are paced at one request/second; per-source CAPTCHA/429 cooldowns prevent
   immediate retries. Do not probe more aggressively to work around a block.
3. **An independently licensed general index remains open.** Wikimedia is a
   narrow supplemental source; self-hosted SearXNG counts as one endpoint
   and forwards queries upstream. Integrate only a licensed/authorized index
   if open-network redundancy results call for it.
4. **Layout-change canaries are unimplemented.** A scheduled fixture refresh
   (fetch each engine's results page periodically, diff the parser's hit
   count) must observe each source's terms and rate limits.
5. **Ranking/dedup tuning** only after an approved live run shows quality
   below 85% — never tune against the held-out split or edit frozen
   labels/thresholds.
