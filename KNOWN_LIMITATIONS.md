# Known limitations — IraLens

Ordered by how much they can mislead a user. Read this before relying on output.

## Verification gaps (not verified in this environment)

1. **Live search engines were not exercised.** This sandbox can reach only
   github.com, api.github.com, registry.npmjs.org, and pypi.org. DuckDuckGo and
   Bing parsers are tested against saved HTML fixtures, not live pages.
   Engine markup changes will show up as `layout_changed`, not as silent wrong results.
2. **Bing is experimental.** Its parser mirrors the `li.b_algo` structure from a
   fixture, and has never run against live Bing. Use `--engine duckduckgo` if
   Bing misbehaves.
3. **Live tests skip here.** The `live` probe now makes a real HTTPS request
   (DECISIONS D-052). In this sandbox it fails, so the live tests skip with a
   reason. They have never run to completion in this environment, so their
   assertions against live sites are unverified.
4. **Browser-engine behavior was not run.** The engine is not installed in this
   sandbox. `install-engine` was not executed. The engine release URL and asset
   names were checked against the GitHub API (release v0.2.4 exists, and the
   Linux/macOS/Windows assets match `engine/install.py`). The downloaded binary was not run.

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
10. **Reads are static only inside research.** Pages that need JavaScript are
    not read; they stay snippet-only and are reported as such.

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

15. **Circuit breaker state is in memory.** It resets on restart. A long-running
    process keeps its breaker state. Separate processes do not share it.
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
18. **Research reads go to a third party.** Each top URL is sent to `r.jina.ai`.
    Use the browser for pages you do not want shared.

## Packaging and project state

19. **License is Apache-2.0**, confirmed by the owner (DECISIONS D-051, D-061).
    `NOTICE` keeps the MIT attribution for the adapted Agent Reach code.
20. **`xiaoyuzhou` is not implemented.** `ARCHITECTURE.md` used to list it as a
    source. It has no module in this bundle.
21. **Not pushed to `main`.** Work is on `arena/a44d3b38-iralens`, and a PR
    into `main` is the route (DECISIONS D-002, D-055).
22. **Web-search and specialized-source tests cover offline behavior only.**
    Parsers, filters, ranking, and the pipeline are tested offline with fakes and
    fixtures. End-to-end behavior against the live web is not tested.

## Not built in this release

23. **No HTTP API.** Access is through the Python facade, the CLI, and the MCP
    server over stdio. See DECISIONS D-053.
24. **No Docker image.** Docker is not available in the build sandbox, so an image
    could not be built or tested. See DECISIONS D-054.
25. **Windows setup is untested.** `scripts/setup.ps1` was written without a
    Windows machine. `scripts/setup.sh` was run here (Linux, Python 3.11). See D-054.
26. **Not published to PyPI.** Install from a checkout (README).
27. **No CI workflow.** The offline checks run locally only (see README and AGENTS.md).
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
31. **`research` exits 0 when every search failed.** The failure is reported in
    `stop_reason` (`search_failed`) and in `limitations`. Agents must read the JSON;
    the exit code does not signal it.
32. **Search failure still raises from `search()`.** `search()` raises
    `SourceUnavailableError` when every engine fails, as the baseline did. Use
    `search_api()` for structured outcomes.
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
