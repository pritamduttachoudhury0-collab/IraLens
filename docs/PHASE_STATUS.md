# Six-phase readiness status (2026-10-13)

This checklist distinguishes implemented work from acceptance evidence. A code
change is not a real-network benchmark, a week's uptime history, legal approval,
or a paid-provider comparison.

| Phase | Work present in the repository | Acceptance status |
|---|---|---|
| 1. Real Internet test | Frozen 200-query / 100-URL runner; this session ran its offline self-test, probed endpoints, verified live GitHub API calls, and ran a GitHub-only research/read/replay check. GitHub API/PyPI were reachable; DuckDuckGo, Bing, SearXNG, and `example.com` were blocked by sandbox egress. | **NOT PASSED.** No open-web full run; no three consecutive passes. A blocked-network result is not a product failure-rate estimate. |
| 2. Repair what breaks | Keyless adapters and parser fixtures remain; added official Wikimedia API backend, hardened SearXNG defaults/configuration, typed optional paid fallback, local OCR option, and tests. Browser installer still depends on GitHub release assets outside this environment's allowlist. | **PARTIAL.** Offline fixtures plus GitHub API behavior are verified; general search parsing and browser installation remain unverified. |
| 3. Sturdier search | Optional private SearXNG Compose stack; public SearXNG instances removed from defaults; Wikipedia API paced at one request/second; persistent SQLite breaker and per-instance SearXNG cooldowns, immediate CAPTCHA/429 backoff and exponential re-probe delay. | **IMPLEMENTED, NOT ACCEPTED.** No seven-day monitored run, provider SLA, or evidence that upstream terms permit the intended scale. |
| 4. Reliable page reading | Local-first HTML/PDF extraction; automatic local browser fallback for JS shells when the engine is installed; opt-in local Tesseract/Poppler OCR for scanned PDFs; remote reader remains opt-in. | **IMPLEMENTED, NOT ACCEPTED.** Browser binary unavailable here; OCR system packages are optional; no real 100-URL extraction benchmark completed. |
| 5. Plug-and-play | Standard-library HTTP API (`/v1/search`, Serper-shaped `/search`, `/v1/read`), API key gate, health and Prometheus metrics, console commands, Dockerfile and Compose stack. | **IMPLEMENTED, NOT ACCEPTED.** Editable install, local wheel build, packaged entry points and CLI help were verified. Docker build/compose and a deployed public URL were not available/verified. No PyPI release or managed support. |
| 6. Prove vs paid | Reference harness now measures 200 labelled queries with gold top-5 quality, availability, failures, latency, domain overlap and per-query IDs. Optional Brave/Serper/Tavily backup can be configured. | **NOT RUN.** No paid API key was provided/configured and no open-network run was possible. No match percentage is claimed. Legal review is also outstanding. |

## Current evidence in this workspace

- **Current offline suite:** `.venv/bin/python -m pytest -q` — **402 passed,
  14 skipped**. The focused suite covering recent reliability, reference API,
  API-key, and SearXNG changes passed **37 tests**. Selected Ruff checks and
  mypy (67 package source files plus the reference harness) are clean. The 14
  skips are network/browser integration checks,
  not passes.
- `scripts/verify_interfaces.py` — **20 passed, 0 failed, 2 blocked**. It
  performed live GitHub API/source calls successfully; the search API returned
  no results because every general search backend was unreachable, and general
  web research therefore had zero sources. These blocked checks are not product
  availability measurements.
- `scripts/live_github_research.py "headless browser"` read six GitHub pages,
  extracted 12 heuristic claims, found no contradiction candidates, and replay
  matched the live report. This validates only GitHub access and local read /
  research plumbing; it does not validate general web search or claim truth.
- General HTTPS probes to DuckDuckGo, Bing, public SearXNG, and `example.com`
  failed at TLS/connect time; GitHub API and PyPI were reachable. This matches
  the environment's hostname allowlist and is not evidence of provider uptime.
- `pip install -e .` built/installed the editable package; `.venv/bin/iralens-api
  --help` and `.venv/bin/iralens serve --help` passed. A non-editable wheel
  built successfully and includes the HTTP API module plus both console entry
  points. No published release or clean-machine deployment is implied.
- `benchmarks/run_benchmark.py --self-test` passed. It validates the harness
  only and is not a search-quality result. No full benchmark run passed.
- PyYAML syntax parsing passed for the root Compose file, standalone SearXNG
  Compose file, and SearXNG settings. Docker and Podman are not installed, so
  validation by an actual Compose engine, image build, startup and smoke test
  remain outstanding.
- Remaining verification to finish Phase 1/2: run the complete frozen command
  on an approved open network, with no query-cache scoring, then repeat until
  three consecutive full runs pass. Keep the command, network label, commit,
  JSON results and any source blocks in `BENCHMARKS.md`.
- Remaining Phase 3 proof: daily, low-volume checks for at least seven days
  from the intended deployment network; publish availability/latency and
  distinguish upstream timeouts from policy blocks. Do not schedule automated
  public-engine traffic before the source review in `SOURCE_POLICY.md`.
- Remaining Phase 4 proof: execute the frozen `urls.json` corpus with a real
  installed browser and, separately, scanned PDFs on a host with OCR binaries.
- Remaining Phase 5 proof: build the Docker image, start the stack, run
  authenticated API smoke/load tests behind TLS, and publish a managed endpoint
  only after operational/security review.
- Remaining Phase 6 proof: configure a permitted paid reference provider and
  run the same 200 query IDs through IraLens and the reference API. Report
  top-5 gold-set quality, availability, failure rate, p50/p95 latency, overlap,
  provider spend/quotas, and the definition of “match”. Do not reduce the
  frozen corpus or compare different query sets.

## Claims not supported today

There is no current number for “percent of a paid search API matched”, no
verified uptime/speed promise, no legal clearance for automated public SERP
queries, and no demonstrated production scale. A locally self-hosted API and
SearXNG reduce integration friction; they do not provide a custom web index,
provider SLA, support team, or legal safe harbor.
