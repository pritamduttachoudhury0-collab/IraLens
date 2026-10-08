# Decisions log — Full IraLens

Each entry: the decision, why, and what it trades off. Newest phase last.
Scope and ownership: the session branch is `arena/a44d3b38-iralens`. All work is
committed there. Nothing is pushed to `main`.

## Baseline (before any Full IraLens change)

**D-000 Import the HalfIraLens bundle as delivered, then build on it.**
The bundle was unpacked verbatim (commit `a4b2716`). Changes are additive, so
the existing `HalfIraLens.search(query, limit, backend) -> List[Artifact]`,
the CLI, the MCP server, and the existing tests keep working.
Baseline offline result: `pytest` gave 57 passed, 7 skipped, and 3 failed. All
3 failures are live-network tests (`test_b_scrape_bulk_mixed_results`,
`test_b_rss_structured_data`, `test_b_v2ex_structured_data`). The sandbox can reach
only github.com, api.github.com, registry.npmjs.org, and pypi.org. The live probe
in `tests/conftest.py` is a bare TCP connect to `example.com:443`. That connect succeeds
through the sandbox proxy, but HTTPS requests to those hosts fail (curl returns 000).
So the probe passed and these tests ran instead of skipping. This is a harness weakness
to fix (probe with an HTTP request, not a TCP connect). Recorded here, not hidden.

**D-001 License conflict, not resolved silently.**
The repo's `LICENSE` is MIT (`Copyright (c) 2026 Pritam Dutta Choudhury`). The
bundle's `pyproject.toml`/`NOTICE` declare Apache-2.0. I kept the repo's MIT
`LICENSE` file untouched and did not write the bundle's Apache text. The
`pyproject.toml` license field still says Apache-2.0. The owner must choose one.
`LICENSE_AUDIT.md` §9 wrongly says LICENSE was "added" in this audit; it was kept.

**D-002 Branch.** The brief said "push to main". The session is bound to
`arena/a44d3b38-iralens`, so all commits and the push go there. Opening a PR to
`main` is left to the user.

**D-003 `.venv/` and `*.egg-info/` added to `.gitignore`.** Local virtualenvs
must never be committed.

## Phase 1 — Unified search

**D-010 Keep the legacy surface, add a structured surface beside it.**
`HalfIraLens.search()` returns `List[Artifact]` as before. `search_api()` returns
a typed `SearchResponse`. The legacy `web-search` source's `fetch("query")` now
delegates to the new engine, so the existing test monkeypatch still works.

**D-011 Legacy `backend` names are aliases.** `semantic-search` maps to Exa.
`browser-search` maps to the DuckDuckGo then Bing chain. New engine names are
`duckduckgo` and `bing`.

**D-012 Empty results return `[]` instead of raising.** Before, a query with no
results on every backend raised `SourceUnavailableError`. Now it returns `[]`
when every engine answered and none had hits. It still raises when engines
failed (`no_results_reason == "engines_failed"`). This makes "no results" and
"engine blocked" distinguishable. It is a deliberate, documented behavior change.

**D-013 Failure taxonomy.** Each engine outcome has a `kind`: `ok`, `empty`,
`captcha`, `rate_limited`, `layout_changed`, `transient`, `unavailable`.
Captcha and rate-limit signals are never retried. Transient failures are retried
with exponential backoff, capped by `Settings`.

**D-014 Fallback and agreement.** An engine chain runs until `search_min_engines`
engines return hits (default 2). Hits from more than one engine get
cross-engine agreement. An engine that is empty or failed triggers a recorded
fallback to the next one. With a single engine, the agreement factor is
uninformative (always 1.0). The `score_breakdown` shows this.

**D-015 Filters: native, emulated, post-filtered, or unsupported. Never silent.**
Each backend declares a mode per filter, and the response reports the mode for
every engine. `include_domains` and `file_type` are emulated through the
`site:` and `filetype:` operators on DuckDuckGo and Bing. Exa has no verified
argument for these, so they are post-filtered. Unknown dates are kept and counted
(`date_unknown_kept`). `language` and `region` are reported `unsupported` where
the engine cannot apply them. They are not faked by post-filtering.

**D-016 Reformulation is deterministic.** Strategies are original, keywords,
sub-question, and synonym, capped at `reformulate_max_queries`. Every query
carries its strategy so results are traceable. An LLM expander can be plugged
in later (Phase 3 interfaces); it is not the default.

**D-017 Dedup in two passes.** Pass 1 merges canonical URL matches (tracking
params, fragments, `www.`, and trailing slash removed; path case kept). Pass 2
merges near-duplicate titles (token Jaccard ≥ 0.85) only on the same registrable
host. Every merge is logged with its reason.

**D-018 Ranking is explainable.** Score = weighted mean of relevance, authority,
freshness, and agreement. Weights, half-life, and authority table all come from
`Settings`. Each factor is returned in `score_breakdown`. Authority is a
domain-suffix table (`.gov` 0.9, `.edu` 0.85, `.org` 0.6, default 0.5). This is a
coarse heuristic, not a quality measure. It is documented as such.

**D-019 Browser-driven engines parse pure HTML.** The engine returns rendered
HTML, which is parsed offline (`html.parser`). The parsers are tested against
saved fixtures. Live behavior of DuckDuckGo and Bing was not verified here
(no network). Bing is EXPERIMENTAL. See KNOWN_LIMITATIONS.md.

**D-020 Settings, not magic numbers.** Every limit, weight, and TTL is in
`halfiralens/settings.py`, read from config or `HIL_<KEY>` env vars.

**D-021 Artifact gets an optional `provenance` field.** It is emitted only when
non-empty, so existing JSON consumers see no change.

**D-022 Shared engine is guarded.** Concurrent searches go through a
`ConcurrencyGate` (default 2 slots) with an acquire timeout. The slot is released
even on error.

## Phase 2 — Reliability, caching, security, resources
(see sections below as they are added)

## Phase 3 — Research intelligence
(see sections below as they are added)
