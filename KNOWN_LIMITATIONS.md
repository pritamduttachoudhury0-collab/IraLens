# Known limitations — IraLens

Ordered by how much they can mislead a user. Read this before relying on output.

## Verification gaps (not verified in this environment)

1. **Live search engines were not exercised.** This sandbox can reach only
   github.com, api.github.com, registry.npmjs.org, and pypi.org. DuckDuckGo,
   Bing, and SearXNG parsers are tested against saved fixtures/payloads, not
   live pages. Engine markup changes will show up as `layout_changed`, not as
   silent wrong results. See the verification record at the end of this file.
2. **Bing is experimental.** Its parser mirrors the `li.b_algo` structure from a
   fixture, and has never run against live Bing. Use `--engine duckduckgo` if
   Bing misbehaves.
3. **Live tests skip here.** The `live` probe now makes a real HTTPS request
   (DECISIONS D-052). In this sandbox it fails, so the live tests skip with a
   reason (confirmed again 2026-10-10: 15 skipped). They have never run to
   completion in this environment, so their assertions against live sites are
   unverified.
4. **Browser-engine behavior was not run.** The engine is not installed in
   this sandbox. `install-engine` was executed on 2026-10-10 and failed
   honestly on the blocked download CDN (github release asset storage is not
   in this sandbox's allowlist); it printed the exact manual URL
   (`install-engine --print-url` prints the same) and exited 2 with
   `browser_engine_unavailable`. The downloaded binary was therefore never
   run, and no checksum is claimed (upstream publishes none). Core search and
   read work without the engine (D-076/D-077).

## Research quality

5. **Claims are heuristic.** Claims are whole sentences that share a term with the
   question. There is no entailment check, so a sentence can be a claim without
   supporting the answer.
6. **Contradictions are candidates.** Numeric and negation mismatches can be
   false positives: two different measurements, or a negation in an unrelated
   clause. They are labeled `heuristic` and are never a verdict.
7. **Confidence is a formula, not a probability.** It reflects domain count and
   source score. It does not measure truth.
8. **Authority is a suffix table.** `.gov` 0.9, `.edu` 0.85, `.org` 0.6, default
   0.5. This is a coarse heuristic. It is not a credibility assessment and it
   misranks plenty of real sources.
9. **Without an LLM, statements are sentences.** The deterministic synthesizer
   quotes the best-scoring claim per cluster. It does not paraphrase or
   reconcile.
10. **Research now uses local-first auto reading, but the browser path is
    unverified here.** JS-shell pages trigger the local browser when its engine
    is installed; without that optional binary, research can remain snippet-only.
    The full JS-heavy URL benchmark has not run on an open network.

## Search behavior

11. **Date and language filters are approximate.** Date filters are post-filtered,
    and results with no date are kept and counted. Language and region are
    unsupported on engines that cannot apply them (reported per engine).
12. **Exa filters are post-filtered.** The bridge tool is called with only the
    arguments known to work, so domain and date filters do not reach Exa.
13. **Empty results are not an error; total failure is.** `search()` returns `[]`
    when every engine answered with no hits (D-012). It still raises
    `SourceUnavailableError` when every engine failed, as the baseline did.
    `search_api()` reports the same case in its `outcomes` instead of raising.
14. **Cross-engine agreement needs two engines.** With one engine configured, the
    agreement factor is always 1.0 and tells you nothing.

## Reliability and security

15. **Search cooldowns are now persisted locally, but are not a distributed
    service.** Engine circuits and SearXNG instance cooldowns use a private
    SQLite file under `IRALENS_HOME/state`; they survive restarts and coordinate
    local processes. Multi-host locking, external health storage, and week-long
    operational history are not implemented. A read-only/unavailable state
    directory fails open.
16. **Injection flags are heuristic.** They cover common English phrasings. They
    miss paraphrases and other languages. They are advisory, not a boundary.
17. **Private-address check: literal plus fetch-time DNS validation.**
    `normalize_public_http_url` checks the URL text. In addition, the static
    reader's fetch (`security.safe_urlopen`, D-072) resolves the hostname,
    refuses any private/loopback answer (including mixed public+private
    answers, a rebinding signature), pins the validated address for the actual
    connection, and re-validates every redirect hop. Remaining gap: sources
    with their own HTTP clients (RSS via `requests`, constant GitHub API
    targets) still validate only the literal URL.
18. **Remote reading remains an opt-in third party.** Direct HTML/PDF reads
    and browser rendering are local. If `IRALENS_READ_REMOTE_READER_ENABLED=1`
    and the local direct read fails, the URL is sent to `r.jina.ai`. Search
    queries still go to the configured search engines. See README Privacy.

## Packaging and project state

19. **License is Apache-2.0**, confirmed by the owner (DECISIONS D-051, D-061).
    `NOTICE` keeps the MIT attribution for the adapted Agent Reach code.
20. **`xiaoyuzhou` is not implemented.** `ARCHITECTURE.md` used to list it as a
    source. It has no module in this bundle.
21. **Not published as a release.** This session's work is on
    `arena/10d16107-iralens`; a reviewed pull request and release are still
    required before end users can install a stable published version.
22. **Web-search and specialized-source tests cover offline behavior only.**
    Parsers, filters, ranking, and the pipeline are tested offline with fakes and
    fixtures. End-to-end behavior against the live web is not tested.

## Not built in this release

23. **HTTP API exists but is not a managed service.** The `iralens-api`
    executable and `iralens serve` command expose `/v1/search`, Serper-shaped
    `/search`, `/v1/read`, `/health`, authenticated `/v1/health/search`, and
    authenticated `/metrics`. Non-loopback binds require a 32-character
    service key. It is a single-node standard-library server;
    it has no hosted URL, SLA, customer support, TLS termination, or multi-node
    health store.
24. **Docker files exist, but the image/Compose stack are unverified here.**
    `Dockerfile` and root `docker-compose.yml` add IraLens plus private SearXNG.
    Build and startup must be tested on a host with Docker and outbound access.
25. **Windows setup is untested.** `scripts/setup.ps1` was written without a
    Windows machine. `scripts/setup.sh` is Linux/macOS-oriented.
26. **Not published to PyPI.** Install from a checkout (README). Publishing
    remains gated on the open-network benchmark, security review, source/legal
    review, and a release decision.
27. **CI runs offline gates only.** `.github/workflows/ci.yml` runs pytest,
    ruff, and mypy. Live search, browser startup, Docker, and seven-day
    monitoring are not exercised in CI.
28. **Packaging metadata warnings: resolved** (D-061). The SPDX license form is
    used and the build requires setuptools 77 or newer.

## Found and verified at the release gate

29. **Live research was verified only over GitHub content.** The sandbox cannot
    reach web search engines or the web reader. Public `research` ran and reported
    `search_failed` (zero sources). The real-data run used GitHub's public
    operations (DECISIONS D-064). Contradiction candidates did not occur in any
    live run. The contradiction path is covered by tests on constructed evidence,
    not by live data.
30. **GitHub corpus needs keyword queries.** Repository search requires every term
    to match, so a long question returns no results (D-065).
31. **`research` exits 4 when every search failed** (D-079, updated
    2026-10-10). The failure is still reported in `stop_reason`
    (`search_failed`) and in `limitations`, and the JSON is still printed;
    the exit code now signals it too.
32. **Search failure still raises from `search()`.** `search()` raises
    `SearchEnginesFailedError` (a `SourceUnavailableError` subclass,
    `error_type: search_engines_failed`) when every engine fails, and the CLI
    exits 4 for it (D-079). Use `search_api()` for structured outcomes.
33. **Browser engine download: optional checksum, still none published upstream.**
    The installer verifies a SHA-256 when one is supplied (`install-engine
    --checksum` or `IRALENS_ENGINE_SHA256`) and aborts on mismatch. The v0.2.4
    release publishes no checksum asset (checked via the GitHub API), so by
    default integrity rests on HTTPS plus the startup probe — and the install
    report says `checksum_verified: false` rather than claiming otherwise.
    Installation is now atomic (probe-before-replace, D-070), so a failed
    download can never destroy a working engine. In this sandbox the asset CDN
    (`objects.githubusercontent.com`) is unreachable, so the download itself
    was not executed here.
34. **Tested on Python 3.11 only** (Debian-based sandbox). `requires-python` says
    3.10 or newer; 3.10 has not been run.
35. **Bandit findings reviewed, not fixed.** Low findings are `assert` statements,
    `try/except/pass`, and `subprocess` calls with fixed arguments. The Medium
    `urlopen` findings are covered in D-063.
36. **Redirects are re-checked on the reader path (D-072).** Every hop is
    re-validated against the public-URL policy, https→http downgrades and
    chains longer than 5 hops are refused, and connections are pinned to
    validated addresses. Verified by offline tests with a local server and
    simulated DNS; not exercised against live redirectors from this sandbox.
    Paths that do not use `safe_urlopen` (see item 17) are unchanged.
37. **Windows setup is untested** (item 25 still applies).

## Added at the final production remediation

38. **Ranking benchmark is a labeled replacement.** The original 20-case audit
    benchmark was never committed and could not be recovered (the repository
    history is a single squash-merged release commit). `scripts/ranking_benchmark.py`
    is a reconstructed 20-case benchmark of synthetic SERPs; its numbers must
    not be quoted as results from the original benchmark. Measured on this
    branch: MRR 0.875 (legacy) -> 0.975 (new), nDCG@5 0.908 -> 0.982.
39. **Low-coverage queries still lean on engine rank.** When a query shares few
    terms with the right page, the rank component dominates and a high-ranked
    distractor can win (case 5 of the benchmark fails under both scorers).
40. **Soft-404 detection is heuristic.** Short bodies with not-found markers
    are classified as `soft_404`; novel 404 page designs can slip through, and
    short real pages that mention "404" are protected only by the length bound.
41. **Live verification still blocked in this sandbox.** Only github.com,
    api.github.com, registry.npmjs.org, and pypi.org are reachable. Live
    search engines, the reader service, the engine asset CDN, and all live
    integration tests remain unverified here; `scripts/verify_interfaces.py`
    reports them as BLOCKED rather than PASS/FAIL.
42. **CJK tokenization uses character bigrams.** Adequate for matching, not a
    word-segmentation model; rare-compound queries can still miss.

## Added with the D-076...D-079 hardening (2026-10-10)

43. **SearXNG must be explicitly configured.** Public instances have been
    removed from defaults because their owners' availability, privacy policy,
    terms, and upstream mix vary. The compose stack supplies a private instance,
    but its upstream engines, terms, and normal-network behavior are not
    verified here.
44. **Direct-fetch User-Agents may hit bot walls.** The free engines and the
    direct reader fetch with a browser-like UA; some sites answer such fetches
    with a challenge. The chain classifies the block honestly (`captcha`,
    `rate_limited`) and the browser engine, when installed, is the rendered
    fallback (D-076).
45. **Local HTML extraction is lossy for complex/JS pages.** The stdlib
    extractor (D-077) has no DOM, CSS layout, or JavaScript. Tables become
    pipe rows, widgets disappear. Use `mode=browser` when fidelity matters.
46. **The remote reader is opt-in and third-party.** With
    `IRALENS_READ_REMOTE_READER_ENABLED=1`, URLs that fail the direct local read
    may be sent to r.jina.ai. It is off by default.
47. **Paid-provider comparison is implemented but not measured.** The optional
    reference phase now reports the same labelled top-5 gold hit rate for IraLens
    and the configured paid provider, plus availability, errors, p50/p95, and
    domain overlap. No key was configured and this environment cannot reach the
    provider; therefore no parity percentage or ranking-equivalence claim can
    be made.

## Verification record (2026-10-10, sandbox with allowlisted egress)

Environment: Debian, Python 3.11.2, egress limited to github.com,
api.github.com, codeload.github.com, registry.npmjs.org, pypi.org,
files.pythonhosted.org. Exact commands and outcomes:

- `./scripts/setup.sh` — venv + editable install + health check: **ok**.
- `.venv/bin/python -m pytest -q` — **328 passed, 15 skipped** (the 15 are the
  live tests; skipped honestly, no general egress).
- `ruff check --select E9,F,B .` — **clean**. `mypy iralens` — **clean, 63
  files** (unstubbed third-party imports ignored via `[tool.mypy]`).
- `.venv/bin/python scripts/verify_interfaces.py` — **20 PASS / 0 FAIL /
  2 BLOCKED** (of 22). PASS includes three live GitHub checks (api.github.com
  is reachable) and the D-079 exit-code contract under real engine failure
  (`engines_failed` exits 4). BLOCKED: "search-api reached at least one
  engine", "research found web sources" — no general egress to verify the
  search engines themselves.
- `.venv/bin/python -m pytest tests/test_integration_live.py -q` — **15
  skipped** (network probe fails here; never run to completion).
- `iralens install-engine` — **failed honestly** (blocked download CDN):
  `browser_engine_unavailable`, manual asset URL printed, exit 2. No engine
  binary was run; no checksum is claimed. `install-engine --print-url`
  verified (exact URL).
- PyPI name `iralens` — **available** (404 on both endpoints).
- **Not verified here, must run somewhere with open Internet:** live
  DuckDuckGo/Bing/SearXNG result parsing, the optional r.jina.ai reader, the
  engine download/exec path, and Windows setup.

*Rebuild provenance:* the D-076...D-079 changes were first written in a local
commit (38eb955) that was never pushed and is unrecoverable; this is the
rebuild from the handoff specification (DECISIONS.md provenance note). Every
claim above was re-tested in this environment on 2026-10-10.

## Additions, 2026-10-11 (benchmark + hardening)

48. **The Step-2 benchmark thresholds are UNVERIFIED from the build sandbox.**
    The sandbox egress policy allows only `github.com`, `api.github.com`,
    `codeload.github.com`, `registry.npmjs.org`, `pypi.org` and
    `files.pythonhosted.org`; every search engine and general web page is
    unreachable (verified with `curl` and the runner's preflight probe). A
    `--limit 3` smoke run therefore reported availability 0.00 / extraction
    0.00 and correctly refused to claim anything. The full three-consecutive-run
    acceptance must be executed on an open network:
    `python benchmarks/run_benchmark.py --all --with-load --network home`.
49. **PDF text extraction needs `pypdf`; scanned-page OCR needs host tools.**
    OCR auto-attempts for image-only PDFs when `pdftoppm` and `tesseract` are
    installed, and defaults to five pages in English (disable with
    `IRALENS_PDF_OCR_ENABLED=false`). Without the tools, `doctor` and the
    typed `pdf_ocr_support_missing` read error say what is missing. OCR is
    subprocess-bounded and never uploads the PDF. Neither OCR tool is
    installed in this workspace, and the arXiv/RFC corpus remains unverified.
50. **The JS-shell heuristic can over-trigger.** Any static page under ~150
    visible characters (or containing an "enable JavaScript" notice) is
    re-read through the browser when the engine is installed. The cost is one
    extra render; the static text is kept if rendering is not better. Thresholds
    and pages in `benchmarks/urls.json` measure this on a real network.
51. **The private-host allowlist applies to search transports only.** Page reads
    keep the strict public-URL policy, so a self-hosted SearXNG cannot be used
    as a general reader, and any user who enables
    `IRALENS_ALLOWED_PRIVATE_HOSTS` should remember that a *search* response
    containing links to private hosts still cannot be fetched. Link-local and
    metadata names are refused even when allowlisted.

## Verification record (2026-10-11, same sandbox, allowlisted egress)

- `.venv/bin/python -m pytest -q` — **378 passed, 14 skipped** (the live suite
  skips without general egress; the CLI-help purity test is now offline).
  Previous records said 328 passed/15 skipped.
- `ruff check --select E9,F,B iralens tests benchmarks` — **clean**.
  `mypy iralens --ignore-missing-imports` — **clean, 64 files**.
- `python benchmarks/run_benchmark.py --self-test` — **PASSED** (harness
  metrics, verdicts, table and `results.json` round-trip validated against a
  synthetic system; explicitly not a measurement).
- `python benchmarks/run_benchmark.py --all --skip-load --limit 3 --network
  datacenter` — ran end to end, **FAIL by design** on a blocked network, exited
  non-zero, appended no row.
- Install-engine EXDEV — **reproduced for real** (`TMPDIR=/dev/shm`, cache on a
  different device) before the fix and **verified fixed** after it.
- Dangling engine shim — **reproduced for real**: read 60.2 s, search 181 s
  before D-084; read 1.07 s, search 0.87 s after it.
- **Not verified here, must run on an open network:** live engine parsing
  (DuckDuckGo/Bing/SearXNG), the real thresholds in `benchmarks/`, PDF
  extraction on real papers, the r.jina.ai reader, and engine
  download/startup of a real binary.

## Verification record (2026-10-13, restricted egress)

- `.venv/bin/python -m pytest -q` — **402 passed, 14 skipped**. The latest
  focused run of reliability, reference-provider, API-key, and SearXNG changes
  passed (**37 tests**). Skips are live/browser integration cases; they are
  not counted as passes.
- `.venv/bin/ruff check --select E9,F,B iralens tests benchmarks` — **clean**.
  `.venv/bin/mypy iralens --ignore-missing-imports` — **clean, 67 files**;
  the new `benchmarks/reference_api.py` also passes mypy on its own.
- `.venv/bin/python scripts/verify_interfaces.py` — **20 PASS / 0 FAIL /
  2 BLOCKED**. Live GitHub API/source and MCP stdio contract checks succeeded;
  the general search/research checks were BLOCKED because all configured search
  engines were unreachable from this sandbox.
- `.venv/bin/python scripts/live_github_research.py "headless browser"` — read
  six GitHub pages, returned 12 heuristic claims, no contradiction candidates,
  and `replay_matches_live_run: true`. This verifies only GitHub source access
  and the local research/read/replay path; the claims remain unverified and it
  does not test open-web search quality.
- `.venv/bin/python benchmarks/run_benchmark.py --self-test` — **passed** as a
  synthetic harness check only. No real benchmark query set was completed, so
  there are no three consecutive passes or paid-provider parity results.
- `docker` and `podman` are unavailable. The Docker image and Compose stack
  were not built or started. No paid reference key was configured, no seven-day
  operational monitoring was done, and no legal review was obtained.

## Verification record (2026-10-16, v0.2.0, restricted egress)

- `.venv/bin/python -m pytest -q` — **428 passed, 14 skipped, 0 failed**.
- `.venv/bin/ruff check --select E9,F,B iralens tests scripts` — **clean**.
- `.venv/bin/mypy iralens --ignore-missing-imports` — **clean across 67 source files**.
- `.venv/bin/python benchmarks/run_benchmark.py --self-test` — **PASSED**.
- `scripts/daily_watch.py` — **2/2 passed** locally against live GitHub
  endpoints, writing JSON diagnostics.
- Concurrency limit: raised to 20 (`search_max_concurrent`), with polite
  per-site limits (`search_per_site_concurrent: 3`) and short-term answer
  cache (D-099).
- Browser engine: Obscura v0.2.4 configured as primary optional engine with
  legacy engine backup, stealth OFF by default, localhost-only control binding
  (D-098). Binary download from GitHub CDN remains blocked by sandbox egress.
- PDF reading: NFKC normalization, ligature unwrapping, 20 MB bounded limit,
  local Poppler retry, and automatic local OCR attempt with honest tool reporting (D-096, D-097).
- Packaging: bumped to v0.2.0. Docker is unavailable in this sandbox environment
  (`/bin/bash: docker: command not found`), so Docker images and compose stacks
  are not claimed to have run. Live open-web benchmark remains unverified in this sandbox.
