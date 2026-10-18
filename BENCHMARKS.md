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

## The audit-driven suites

The independent audit of 2026-10-17 shipped its own harnesses inside its
evidence bundle, and the repository did not. They are here now, in the
repository, with the corpus hash-verified and never edited:

| command | corpus | result on 2026-10-18 (restricted egress) |
|---|---|---|
| `python benchmarks/acceptance_17.py` | the audit's 17 offline acceptance checks, inverted so that each asserts the defect is **gone** | **17/17** |
| `python benchmarks/run_scraping.py` | 12 read/extract probes (budget, main content, JS shell, script leakage, typed 429, block classification, cache provenance, concurrent saves, typed refusal) | **12/12** |
| `python benchmarks/run_audit3000.py --offline-pipeline` | the frozen 3,000-query corpus through the real engine with a local backend | **14,262/14,262 clean** (100% code score, 100% strict clean) |
| `python benchmarks/run_audit3000.py --replay <audit baseline>` | the audit's own captured 3,000-row run, re-scored with the same predicates | 31,264 checks: **8,435 code-caused**, **5,223 external**, 17,606 clean (code score 73.02%) |
| `python benchmarks/project_score.py` | the projected per-query outcome, with each number traced to the register entry that produces it | **3,000/3,000** with no code-caused failure; **2,955/3,000** with no defect at all; **1,637/3,000** under the strictest reading |
| `python benchmarks/run_audit3000.py --live` | the frozen 3,000-query corpus against the live engines | **not_run** — restricted egress cannot reach the search providers |
| `python benchmarks/measure_focus_split.py` | the subject/request split, scored against the corpus's own recorded topic | **2,619 / 2,950 = 88.78%** recovered exactly |
| `python benchmarks/derive_request_markers.py` | the request lexicon, learned on a **training half** and scored on the **held-out half** | **89.02%** held-out |
| `python benchmarks/calibrate_coverage.py` | the coverage metric against the audit's 45 manual labels, and the E-03 contamination in them | 1,620 low over the baseline; recall 0.667 on labels of which **17 are contaminated** |
| `python benchmarks/ab_query_forms.py` | ten hand-labelled **real provider** captures: the query form A/B, and the old and new coverage metric against the same human labels | focus **7/7** where the loose engine gives **0/10**; metric agrees **10/10** (was 6/10) |

The baseline replay is the honest "before" number and it is reproducible: the
audit's own `records/iralens.jsonl`, scored by the predicates in
`benchmarks/run_audit3000.py`. (The check count rose from 28,521 to 31,264
when the relevance family was split — see below. The failure totals did not
change: the baseline genuinely did hide its low coverage, so those findings
were correctly code-caused.) The 8,435 code-caused failures it reports are
the defects this repository fixed (dates labelled as publication dates,
missing Wikipedia snippets, unenforced `site:` scope, an unconfigured engine
in the default chain, and results that did not address the question). The
5,223 external failures are provider-side captchas and throttles; no code
change removes them, and the runner never counts them as IraLens defects.

### The relevance family is split, on purpose

The audit's own S-RL entry carries `root_status: "Unknown"`. It established
that IraLens presented unrelated filler as usable evidence — a handling
defect, ours, and the one its `expected` clause names ("report low coverage
rather than presenting unrelated filler as usable evidence"). It could **not**
establish why the provider returned that filler. The audit manually confirmed
a relevance defect on **45 of 3,000** queries; the other **2,955** were never
reviewed and are recorded as `unresolved`.

So the harness scores two families, not one:

| family | what it means | cause | who fixes it |
|---|---|---|---|
| `RL` | the response **hid** low coverage (`no_results_reason: "none"` while the coverage verdict is `low`) | `code` | E-22 |
| `RLR` | the results themselves are weak | `unresolved` | not attributable — the audit says so itself |

A lexical term-overlap proxy stands in for the audit's manual review, and it
is a blunt instrument: against the audit's own 45 manual confirmations it has
precision **0.016** and recall **0.756** — it flags 2,098 queries the audit
never examined, and misses 11 the audit did confirm. That is why the proxy's
verdict is reported as `unresolved`: never charged to the code, never counted
as a pass.

The `--live` run needs an unrestricted network. It is the only number here
that is **not** measured, and it is reported as `not_run` rather than as a
pass.

### What the 3,000-query score should be

`benchmarks/project_score.py` replays the audit's captured baseline, applies
the documented fix semantics of each register entry, and reports the
per-query outcome. It is a **projection, not a measurement** — only a live
run measures a score — but it is traceable: every number below names the
entry that produces it.

| reading | score | what it means |
|---|---|---|
| no **code-caused** failure | **3,000/3,000** (100%) | every defect this project can fix is fixed; this is the number that should be 100% |
| no defect at all | **2,955/3,000** (98.5%) | the audit's own manually confirmed relevance defects (45) are upstream and remain; the system now *discloses* them instead of hiding them |
| no finding at all (strictest) | **1,637/3,000** (54.6%) | the coverage proxy still objects to 1,363 queries, 1,318 of which the audit never reviewed |

Two conditions move the middle and lower rows, and they are outside the
code's control:

* **Providers healthy.** The 3,000 `S-AV` failures in the baseline were
  provider-side (DuckDuckGo captcha, a rate-limited Bing, an unconfigured
  `mcporter` engine). With working providers they disappear. With degraded
  providers every one of the 3,000 queries carries an external failure and
  the raw index is 0/3,000 — exactly as in the baseline, for reasons no
  patch can reach.
* **The relevance gap.** The audit confirmed 45 relevance defects by hand.
  Its own `root_status` for that family is "Unknown", and 2,955 queries were
  never reviewed at all, so the true count is a range, not a number. The
  projection reports the range rather than picking the flattering end of it.

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
5. **Ranking/dedup tuning** is not certified by the frozen acceptance benchmark.
   An evidence-driven improvement branch now contains deterministic ranking,
   intent, language and resilience changes; see `docs/improvements/FINAL_REPORT.md`.
   The 300-query improvement holdout was frozen before those changes, but could
   not be run against live engines in restricted egress. Its P@5 is **NOT
   MEASURED**. Do not treat offline fixtures as a benchmark pass, and never tune
   against frozen labels/thresholds.
