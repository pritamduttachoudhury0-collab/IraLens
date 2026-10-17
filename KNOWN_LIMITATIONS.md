# Known limitations — IraLens

Ordered by how much they can mislead a user. Read this before relying on
output. Everything here is stated as a product limit; dated verification
records at the end show exactly what was exercised and where.

## Live behavior

1. **Live results depend on your network and the search sources you
   configure.** The free engines fetch public results pages over HTTP; some
   networks and some engines answer such fetches with bot checks, captchas,
   or rate limits. The chain classifies these honestly (`captcha`,
   `rate_limited`) rather than inventing results, and reports
   `engines_failed` when nothing answered. Run on a normal network,
   `iralens --json doctor` and `search-api` outcomes tell you the live
   state; nothing else can.
2. **Engine markup can drift.** Parsers are fixture-tested. If an engine's
   markup changes, the outcome is `layout_changed`, not silently wrong
   results.
3. **Bing is experimental.** Its parser mirrors the `li.b_algo` structure and
   is less exercised than DuckDuckGo's. Use `--engine duckduckgo` if it
   misbehaves.
4. **The integrated browser engine ships bundled and is startup-probed.**
   Core Obscura source (`bd9529418b7fa975b50eabeac415cfaaa865a588`, base tag
   `v0.2.4`) is vendored under `iralens/_internal/browser_engine/vendor/obscura`
   and runs out of the box via `py_mini_racer`'s isolated V8 runtime without
   runtime downloads from GitHub Releases (`iralens install-engine --status`
   and `iralens doctor` report the live state). Pixel-level GPU rendering
   (`obscura-render` / `wgpu` screenshots) and Chrome DevTools Protocol server
   (`obscura-cdp`) are excluded from the default vendored runtime to keep the
   distribution lightweight (`1.2 MB` wheel). Prebuilt `mini-racer` wheels
   cover Linux (`x86_64`, `aarch64`), macOS (`x86_64`, `arm64`), and Windows
   (`AMD64`); on platforms without `mini-racer`, the runtime falls back to a
   persistent Node.js V8 VM worker if `node` is installed, or reports
   `browser_engine_unavailable`.
5. **Direct-fetch User-Agents may hit bot walls.** The free engines and the
   direct reader fetch with a browser-like UA; bot-walled sites answer with
   a challenge. The browser engine, when installed, is the rendered
   fallback.

## Research quality

6. **Claims are heuristic.** Claims are whole sentences that share a term
   with the question; there is no entailment check, so a sentence can be a
   claim without supporting the answer.
7. **Contradictions are candidates.** Numeric and negation mismatches can be
   false positives: two different measurements, or a negation in an unrelated
   clause. They are labeled `heuristic` and are never a verdict.
8. **Confidence is a formula, not a probability.** It reflects domain count
   and source score; it does not measure truth.
9. **Authority is a suffix table.** `.gov` 0.9, `.edu` 0.85, `.org` 0.6,
   default 0.5. A coarse heuristic, not a credibility assessment.
10. **Without an LLM, statements are quotations.** The deterministic
    synthesizer quotes the best-scoring claim per cluster; it does not
    paraphrase or reconcile. Research output is leads with stated limits,
    not verified answers.
11. **The contradiction path is covered by constructed-evidence tests.** Real
    contradictory corpora are rare in practice, so that path is exercised by
    design, not by a large live corpus.

## Search behavior

12. **Date and language filters are approximate.** Date filters are
    post-filtered and undated results are kept and counted. Language and
    region are unsupported on engines that cannot apply them (reported per
    engine).
13. **Exa bridge filters are post-filtered.** The bridge is called with only
    the arguments known to work, so domain and date filters do not reach the
    provider.
14. **Cross-engine agreement needs two engines.** With one engine configured,
    the agreement factor is always 1.0 and tells you nothing.
15. **Low-coverage queries lean on engine rank.** When a query shares few
    terms with the right page, a high-ranked distractor can win. The
    synthetic ranking benchmark keeps a failing case of this kind on record
    rather than tuning it away.
16. **CJK matching uses character bigrams.** Adequate for matching; not a
    word-segmentation model, so rare-compound queries can still miss.
17. **SearXNG upstreams keep their own rules.** A configured SearXNG instance
    forwards queries to its enabled upstream engines; their terms, rate
    limits, and availability apply. Review `docs/search.md` (source policy)
    before deployment.

## Reading

18. **Local extraction is lossy for complex pages.** The stdlib extractor has
    no DOM, CSS layout, or JavaScript. Tables become pipe rows; widgets
    disappear. Use `mode=browser` when fidelity matters.
19. **Soft-404 detection is heuristic.** Short bodies with not-found markers
    are classified `soft_404`; novel 404 page designs can slip through, and
    short real pages mentioning "404" are protected only by a length bound.
20. **The JS-shell heuristic can over-trigger.** Any static page under ~150
    visible characters (or with an "enable JavaScript" notice) is re-read
    through the browser when the engine is installed. The cost is one extra
    render; the static text is kept if rendering is not better.
21. **OCR needs host tools and is bounded.** Scanned-page OCR runs only when
    `pdftoppm` and `tesseract` are installed, defaults to five pages in
    English, and never uploads the document (`IRALENS_PDF_OCR_ENABLED=false`
    opts out). Without the tools, scans fail with typed
    `pdf_ocr_support_missing` and `doctor` says what is missing. OCR quality
    on a broad scan corpus is not yet verified.
22. **The remote reader is opt-in and third-party.** With
    `IRALENS_READ_REMOTE_READER_ENABLED=1`, URLs that fail the direct local
    read are sent to `r.jina.ai`. It is off by default.

## Reliability and security

23. **Circuit state is local, not distributed.** Engine circuits and
    per-instance cooldowns persist in a private SQLite file under
    `IRALENS_HOME/state`; they survive restarts and coordinate local
    processes. Multi-host locking, external health storage, and long
    operational history are not implemented. A read-only state directory
    fails open.
24. **Injection flags are heuristic.** They cover common English phrasings,
    miss paraphrases and other languages, and are advisory — not a boundary.
25. **The private-address check is literal plus fetch-time.** The literal URL
    check covers everything; DNS resolution, answer validation, and redirect
    re-validation cover the fetch path (`security.safe_urlopen`). Sources
    with their own HTTP clients (RSS, the constant GitHub API targets)
    validate only the literal URL.
26. **The private-host allowlist applies to search transports only.** Page
    reads keep the strict public-URL policy, so a self-hosted SearXNG cannot
    be used as a general reader, and a *search* response linking to private
    hosts still cannot be fetched. Link-local and metadata names are refused
    even when allowlisted.

## Platform and packaging

27. **Not published to PyPI.** Install from a checkout (README). Publishing
    follows the open-network benchmark and release review.
28. **The HTTP API is a single node.** No hosted URL, SLA, TLS termination,
    multi-node health store, or managed uptime. Put a TLS reverse proxy and
    resource limits in front of it for anything beyond local use.
29. **Docker files are provided, not yet verified on a live host.** Build
    the image and start the Compose stack on the target host before relying
    on them. The API itself runs fine without Docker (`iralens serve`).
30. **Windows setup is untested.** `scripts/setup.ps1` is provided but has
    not run on a Windows machine; `scripts/setup.sh` is the tested path
    (Linux/macOS).
31. **CI verifies Python 3.10 and 3.12** (offline gates on every push and
    pull request). The primary development interpreter is 3.11.
32. **CI runs offline gates only.** The CI workflow runs pytest, ruff, and
    mypy. Live search, browser startup, Docker builds, and multi-day
    monitoring are not exercised in CI.
33. **`xiaoyuzhou` (podcast) source is not implemented.** No module ships for
    it; the architecture may grow one later.
34. **Paid-provider parity is implemented but unmeasured.** The optional
    benchmark reference phase reports labeled top-5 gold hit rates,
    availability, p50/p95, and domain overlap for both IraLens and a
    configured paid provider — but no provider key has been supplied for a
    run, so no parity claim exists. The Serper-compatible `/search` endpoint
    covers general web search only; proprietary Google features (knowledge
    graph, verticals) are intentionally not synthesized.

## Verification records

Each record states the date, the environment class, and the exact claim.
"Restricted egress" means the verification environment could reach only
package indexes and GitHub — live search engines and arbitrary web pages
were not reachable there, so anything requiring them is listed as not yet
verified.

**2026-10-17, restricted egress (v0.2.1 + integrated browser engine).**

- `.venv/bin/python -m pytest -q` — **442 passed, 14 skipped, 0 failed** (including
  13 end-to-end real-browser integration tests in
  `tests/test_browser_engine_integration.py` against a local HTTP fixture
  server exercising navigation, JS rendering, external `<script src>`, V8 JS
  evaluation, `[ref=N]` accessibility snapshots, clicks, form POST submission,
  301/302 redirect chains, 404/500 error classification, SSRF/redirect
  protection, V8 script/navigation timeouts, RFC 6265 cookies/`localStorage`
  persistence, multi-tab management, and unified `Artifact` schema).
- `ruff check --select E9,F,B .` — clean. `mypy iralens` — clean (74 source
  files).
- Package distribution sizes: wheel `1.2 MB` (`1,239,833` bytes), sdist
  `1.3 MB` (`1,341 KB`), vendored Obscura source tree `4.1 MB` uncompressed.

**2026-10-16, restricted egress (v0.2.1).**

- `.venv/bin/python -m pytest -q` — **428 passed, 14 skipped, 0 failed**. The
  14 skips are live-network and browser-engine tests that skip cleanly
  without general Internet egress.
- `ruff check --select E9,F,B .` — clean. `mypy iralens` — clean (67 source
  files).
- `benchmarks/run_benchmark.py --self-test` — passed (harness math validated
  against a synthetic system; not a measurement).
- `scripts/daily_watch.py` — 2/2 live public GitHub page reads passed.
- Full feature walk and clean-install check: `docs/audits/final-verification.md`.
- Not verified there: live DuckDuckGo/Bing/SearXNG runs, the remote reader,
  the engine binary download, Docker builds, Windows setup.

**2026-10-16, open network (paid-search replacement audit).**

An independent audit on an unrestricted network exercised the live paths the
restricted environment could not; the full report is
`docs/audits/paid-search-replacement-audit-2026-10-14.md`. Headlines: clean
install with the live test suite **416 passed, 0 failed**; DuckDuckGo and
Bing **15/15** each; the frozen 100-query quality slice passed (exact-site
top 3 **24/25**, facts top 10 **25/25**); research answered **12/12** with no
invented citations; page reading **33/60 (55%)**, which is why the reading
repairs above exist; 20-way burst latency exceeded its target at audit time
(p95 18.8 s → sub-second offline after the concurrency rework). Those
numbers are point-in-time measurements, re-collected per the benchmark
schedule in `BENCHMARKS.md`.

**Earlier restricted-egress records (2026-10-09 → 2026-10-13).**

The acceptance audit at `docs/audits/production-acceptance-audit-2026-10-09.md`
records the offline hardening verification in full (adversarial SSRF matrix,
ranking delta, clean-environment install). Interim suite runs grew from
328 → 378 → 402 passing tests as coverage expanded; all are superseded by the
2026-10-16 records above.

## Deliberate non-goals

- Independently crawled search index or vector database.
- Managed endpoint, uptime promise, or multi-node health store.
- LLM-by-default research (the deterministic core stays the baseline).
- Public SearXNG instances bundled as defaults.
- Auto-login to platforms; authenticated sources use credentials or browser
  sessions you provide.
