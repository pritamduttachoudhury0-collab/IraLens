# Known limitations — Full IraLens

Ordered by how much they can mislead a user. Read this before relying on output.

## Verification gaps (not verified in this environment)

1. **Live search engines were not exercised.** This sandbox can reach only
   github.com, api.github.com, registry.npmjs.org, and pypi.org. DuckDuckGo and
   Bing parsers are tested against saved HTML fixtures, not live pages.
   Engine markup changes will show up as `layout_changed`, not as silent wrong results.
2. **Bing is experimental.** Its parser mirrors the `li.b_algo` structure from a
   fixture, and has never run against live Bing. Use `--engine duckduckgo` if
   Bing misbehaves.
3. **Live tests fail here.** Three `test_integration_live` tests fail because the
   sandbox blocks their hosts. The `@live` probe is a TCP connect, which succeeds
   through the proxy. See DECISIONS D-000.
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
13. **Empty results are no longer an error.** `search()` returns `[]` when every
    engine answered with no hits. Before, it raised. This is documented in D-012.
14. **Cross-engine agreement needs two engines.** With one engine configured, the
    agreement factor is always 1.0 and tells you nothing.

## Reliability and security

15. **Circuit breaker state is in memory.** It resets on restart. A long-running
    process keeps its breaker state. Separate processes do not share it.
16. **Injection flags are heuristic.** They cover common English phrasings. They
    miss paraphrases and other languages. They are advisory, not a boundary.
17. **Private-address check is literal.** `normalize_public_http_url` checks the
    URL text, not DNS results. DNS rebinding and private-IP redirects are not covered.
18. **Research reads go to a third party.** Each top URL is sent to `r.jina.ai`.
    Use the browser for pages you do not want shared.

## Packaging and project state

19. **License conflict is unresolved.** `LICENSE` is MIT. `pyproject.toml` and
    `NOTICE` declare Apache-2.0. The owner must choose (DECISIONS D-001).
20. **`xiaoyuzhou` is not implemented.** `ARCHITECTURE.md` used to list it as a
    source. It has no module in this bundle.
21. **No GitHub PR or push to `main`.** Work is on `arena/a44d3b38-iralens`
    (DECISIONS D-002).
22. **Web-search and specialized-source tests cover offline behavior only.**
    Parsers, filters, ranking, and the pipeline are tested offline with fakes and
    fixtures. End-to-end behavior against the live web is not tested.
