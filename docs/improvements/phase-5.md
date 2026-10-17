# Phase 5 — Security hardening for observed classes (D8)

## Changes

- Known Google Translate and `r.jina.ai` result wrappers are unwrapped before
  public-URL validation, canonicalization, deduplication, ranking and citation.
- `is_test_host()` identifies `uat`, `staging`, `stage`, `dev`, `test`, `qa`,
  `sandbox`, localhost/`.local`, and bare IP hostnames. Such results remain visible
  but carry `test_host` and are demoted; they are not silently discarded.
- Search result prompt-injection and invisible-character flags were already scanned
  and flowed through `RankedResult` and `SearchResponse`. Regression tests now assert
  both test-host and prompt-injection flags survive the structured response.
- `search_api`, HTTP and MCP already serialize `SearchResponse.to_dict()`; the new
  degradation, shortfall and result-level security fields therefore use the same
  public serialization path.

## Checks and limits

- Deterministic wrapper, test-host, prompt-injection propagation and response
  serialization fixtures pass offline.
- The benchmark's UAT/translation observations could not be replayed because its raw
  captures are absent. No external red-team website, live page-body injection, or
  agent tool-execution test was performed. Injection detection remains heuristic;
  content is untrusted data and must never be treated as instructions.
