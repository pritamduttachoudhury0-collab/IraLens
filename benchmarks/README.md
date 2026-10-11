# IraLens reliability benchmark

This directory is the acceptance benchmark for using IraLens as a replacement
for a paid search API. It is **frozen**: `queries.json`, `urls.json` and
`thresholds.json` are hashed in `FREEZE.sha256`, the run reports any change,
and nobody — including the authors — tunes them to make a number look better.

| file | what it is |
|---|---|
| `queries.json` | 200 hand-labelled queries: factual, recent news, long-tail, technical/code, navigational, non-English, multi-intent. 140 dev / 60 held-out. Each entry lists acceptable result domains and/or an expected answer phrase. |
| `urls.json` | 100 diverse URLs (news, docs, wikis, blogs, JS-heavy apps, PDFs, paywalled/login-walled) with a phrase that must appear in the extracted main text. |
| `thresholds.json` | the Step-2 acceptance thresholds, as data |
| `run_benchmark.py` | the runner (one command, cache bypassed, realistic pacing) |
| `reference_api.py` | optional comparison against a commercial search API |
| `FREEZE.sha256` | hashes of the frozen files |
| `results/` | per-run output (`results.json` + printed table) — not committed |

## Run it

```bash
# from the repository root, with the venv that has the project installed
.venv/bin/python benchmarks/run_benchmark.py --all --with-load --network home
```

`--all` runs: preflight, search (200 queries), extraction (100 URLs),
redundancy (each engine alone on a sampled query set), honesty (exit codes,
typed errors, SSRF refusal), and the repository's SSRF/content-guard suites.
`--with-load` adds the 30-minute load test (10 queries/min, 5 concurrent) —
without it, the load threshold is reported as `not_run`, never as a pass.

Useful flags:

```bash
--skip-load                 # everything except the 30-minute load test
--split dev|heldout|all     # score one split (held-out is scored separately)
--limit 20 --pace 0         # quick smoke run
--out DIR                   # where results.json goes
--self-test                 # validate the harness offline, no network needed
--allow-cache               # OFF by default: scores must never come from cache
```

Each run prints a table and writes `results.json`. When **every** threshold
passes, the runner appends one row to `BENCHMARKS.md`; when something fails or
could not run, it appends nothing and says what failed.

## What each number means

* **availability** — fraction of queries with 1+ real results. A block page
  (`captcha`) or "every engine failed" is *not* availability.
* **quality** — a query is answered when one of its top-5 results is on an
  acceptable domain or contains an expected phrase. Reported per split; the
  held-out split must land within 5 points of dev and is never tuned against.
* **freshness** — for queries whose subject moves (`recency_days`), the
  fraction with a dated top-5 result inside the window. Informational: most
  public SERPs ship no dates at all, so this is honest context, not a gate.
* **engine redundancy** — each engine is queried *alone* over a
  category-balanced sample; the threshold needs 2+ engines above 80% solo.
  `solo_saver` counts how often an engine was the only one to answer a query,
  so a single engine cannot silently become the whole system.
* **latency** — wall-clock per `search_api` call (p50/p95) and per `read`
  (p95). Nearest-rank percentiles, no interpolation.
* **extraction** — a URL passes when its required phrase appears in the
  extracted main text. `expect: "phrase_or_walled"` marks pages that may
  legitimately refuse (paywall/login wall): those pass with the phrase **or**
  with a typed error, but never with silently empty content. The strict
  all-100 rate is reported too, so nothing is hidden behind the split.
  `requires_js` pages must end up on the browser backend when the static fetch
  cannot produce the text.
* **honesty** — the D-079 exit-code contract through the real CLI, typed
  errors for dead hosts/SSRF targets/unknown sources, plus a shape check over
  every response: an empty answer must name a reason and every failed engine
  outcome must carry a kind. `untyped_failures` must be 0.

## Comparing with a paid search API (optional)

```bash
export SEARCH_API_REFERENCE_KEY=...            # Brave by default
export SEARCH_API_REFERENCE_PROVIDER=brave     # braves | serper | google-cse | tavily | searxng
export SEARCH_API_REFERENCE_CX=...             # only for google-cse
.venv/bin/python benchmarks/run_benchmark.py --all --with-reference
```

The runner replays the same queries through that API and reports domain
overlap at 5 results, mean Jaccard, its availability and its p50 latency. This
is diagnostic only: no threshold depends on it, and different engines finding
different pages is expected. What must not happen — fewer answered queries,
silent failures, block pages returned as results — is what the real thresholds
measure.

## Rules for changing this directory

1. Do not edit `queries.json` / `urls.json` to raise a score. If a label is
   genuinely wrong (a domain is dead, a phrase no longer exists), fix it *and*
   regenerate `FREEZE.sha256`, and say so in `BENCHMARKS.md`.
2. Never lower `thresholds.json`. Fix the system instead.
3. Label anything you could not run as `not_run` with the reason. A run that
   could not reach the Internet is evidence about the network, not a pass.
4. `--self-test` validates the harness with a synthetic system; it is never a
   measurement of IraLens and its output is named accordingly.
