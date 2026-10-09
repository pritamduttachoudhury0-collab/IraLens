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

**D-001 License conflict (resolved by D-051).**  *(Resolved by D-051 and D-061.)*
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

**D-030 Failure taxonomy shared across layers.** `captcha`, `rate_limited`,
`layout_changed`, `transient`, `unavailable`, `empty`, `circuit_open` (see
docs/RELIABILITY.md). Only transient failures are retried. Blocks are never
retried.

**D-031 Circuit breaker per engine, in memory.** Opens after 3 consecutive
failures and skips the engine for 300 s. One half-open probe. Not persisted, so
one bad outage cannot lock an engine out across restarts.

**D-032 Degraded searches are not cached.** A failed or skipped engine means the
response is not stored, so the next call retries live. Empty but healthy
responses are also not cached (`cacheable` requires results). Reason: an empty
result from a transient block must not stick for 15 minutes.

**D-033 Page cache covers static reads only.** Browser reads are never cached,
because the browser can hold session state. Static reads are public. The cache
TTL is 3600 s. This is a conservative choice. The brief asked for cached page
fetches; this covers the static reader and not browser pages.

**D-034 Content guard flags, does not rewrite.** Page content is returned as
fetched. Flags are attached to provenance. Only search titles and snippets get
invisible-character stripping and length caps, because they are short and
rendered into result lists.

**D-035 Non-public result URLs are dropped.** `normalize_public_http_url` is
applied to every hit before ranking. It is a literal-URL check only; DNS
rebinding and private redirects are not covered (see docs/THREAT_MODEL.md).

**D-036 Breaker state lives on the SearchEngine, which the source keeps as a
singleton.** A new facade instance shares it through the source. This was a
choice for the single-process CLI and MCP servers. Multi-process use would need
a shared store, and that is not built.

## Phase 3 — Research intelligence

**D-040 Deterministic core; LLM is an optional adapter.** The planner, claim
extraction, contradiction detection, synthesis, and confidence formula are all
deterministic and inspectable. `research/llm.py` takes any `complete(prompt)`
callable. The facade does not take one yet. Reason: HalfIraLens ships no model
and no key, and an LLM must not be a hidden dependency of the core result.

**D-041 Replayable trace.** The trace records every search response, every
expander decision, every page read, and the date used. `ResearchPlanner.replay`
rebuilds the report from it with no network access. Reason: research output
has to be auditable after the fact. Replay reuses recorded decisions, so it does
not depend on whatever expander the replaying caller has.

**D-042 Stop reasons are explicit.** `coverage_reached`, `budget_exhausted`,
`no_new_sources`, `search_failed`, `max_rounds`. `search_failed` is separate, so
"every engine was blocked" is not reported as "nothing new to find".

**D-043 Claims are sentences; years are not measurements.** Sentences that share
a term with the question become claims. Four-digit years are excluded from the
number comparison. Otherwise any two sources citing different years would look
like a numeric contradiction.

**D-044 Contradictions are candidates only.** Each one is labeled
`confidence: "heuristic"`. The report and the docs state that they are not verdicts.

**D-045 Statement scoring is shared by the deterministic and LLM paths.** The
LLM may propose wording and grouping. `make_statement` computes status and
confidence either way. An LLM proposal that cites an unknown claim id is
rejected as a whole, and the deterministic statements are kept.

**D-046 Research reads only static pages.** It does not drive the browser. This
avoids session state and keeps reads cacheable. It also means it sends each top
URL to `r.jina.ai` (the existing static reader). This was not a decision the
user made explicitly; it inherits the existing reader's behavior. Recorded here
because it is a privacy-relevant data flow. Use `read()` in browser mode for a
URL you do not want shared.

**D-047 Confidence and authority are transparent heuristics.** The formulas and
constants are in `Settings` and `research/synthesis.py`. They are not calibrated
against ground truth, and the docs say so.

**D-048 Engine download URL verified, binary not run.** `engine/install.py` points
at `h4ckf0r0day/obscura` release `v0.2.4`. The GitHub API confirms the release exists, and
that the Linux, macOS, and Windows asset names match the ones the installer requests.
The binary itself was not downloaded or executed in this environment.

**D-049 Tool counts come from code.** MCP tool count is `len(TOOLS)` = 49 at
this commit. README and CAPABILITY_MAP were updated to match. The earlier
"46/47" figures were wrong.

**D-050 Scope cut.** Not built in this pass: a LLM facade hook, a persisted
circuit breaker, a second-language reader, and Bing live verification. Each is
in KNOWN_LIMITATIONS.md.

## Licensing (owner decision)

**D-051 License set to Apache-2.0.** On the owner's instruction, `LICENSE` now holds
the full Apache-2.0 text, fetched from GitHub's license API (`/licenses/apache-2.0`).
This matches `pyproject.toml` and `NOTICE`. The former MIT text is replaced. Reason:
it was the owner's explicit choice from the options offered. The copyright line
"Copyright (c) 2026 Pritam Dutta Choudhury" moved from the MIT file into `NOTICE`.
The MIT attribution for the adapted Agent Reach code is kept in `NOTICE`, as its
terms require.

## Release hardening

Numbers D-037 to D-039 were planned for this work and never assigned. They are
left unused rather than reused, so no entry changes meaning.

Addendum to D-000: the live-probe weakness recorded above is fixed by D-052.

**D-052 Live probe is a real HTTPS request.** `tests/conftest.py` now calls
`urlopen("https://example.com/")` and counts the network as available only on a
2xx/3xx response. The bare TCP connect let the sandbox proxy pass the probe while
HTTPS failed, so live tests ran and failed. Now they skip with a reason. Live
tests still need a real network to be verified.

**D-053 No HTTP API in this release.** The brief asked for one. It is not built,
because a server with auth, request limits, and error mapping needs its own
design and tests, and none were done. The facade, CLI, and MCP stdio server are
the supported interfaces. Revisit as a separate phase.

**D-054 Setup: POSIX tested, Windows not; no Docker image.** `scripts/setup.sh`
was run here in a fresh virtualenv on Linux (Python 3.11): install, `--version`,
and `doctor` pass. `scripts/setup.ps1` was written but has not been run on Windows,
so it is labeled UNTESTED in the file and in the README. Docker is not available in
the build sandbox, so no image was built. The brief asked for one; it is deferred.

**D-055 Branch and PR.** The owner asked for a push to `main`. The session is fixed
to `arena/a44d3b38-iralens`, so the work is pushed there and a pull request into
`main` is opened. No direct push to `main` is made.

**D-056 Type and lint fixes are behavior-neutral except one bug.** mypy is clean on
`halfiralens` (59 files). Ruff `E9,F,B` is clean. The real bug was
`ProvenanceGraph.node()` receiving `kind` twice whenever a contradiction was
recorded. That crashed any research run with contested claims. The attribute is
renamed to `contradiction_kind`, and a regression test runs the full planner
into that path. The other changes are type annotations and one signature fix:
`Source.read_url` now takes `mode` in the base class and every override, as
core.py already assumed. RSS's `limit` moved after `mode`; its only caller passes
it by keyword.

**D-057 MCP errors distinguish caller mistakes.** A missing required argument or a
malformed value now returns `invalid_input` (new `InvalidInputError`), not
`internal_error`. `halfiralens --version` was added as a top-level flag. The
`version` subcommand is kept.

## Release gate (final verification before merge)

**D-058 Claims are prose, not page markup.** The first live run over real GitHub
READMEs produced claims such as `# [PhantomJS](...)`, `<p><strong>`, a `curl`
command, and a table row. `split_sentences` now strips fenced code, HTML, image
and link syntax (keeping link text), heading and list markers, emphasis, table
rows, bare URLs, and shell-like lines, and drops fragments that are mostly symbols.
Regression test: `test_claims_are_prose_not_markup`. The old extractor produced
three markup-bearing sentences on that input; the new one produces none.

**D-059 `--json` works before and after the subcommand.** Agents write
`halfiralens research "q" --json`. That was an argparse error. Every subcommand now
inherits the flag through a parent parser with `default=SUPPRESS`, so the
top-level default still applies when the flag is absent.

**D-060 JSON output serializes results as objects.** `fetch ... --json` (and any
command returning Artifacts) printed Python reprs such as
`"Artifact(title='...')"`, which agents cannot parse. JSON output now converts
objects through `to_dict()` recursively. The untrusted-content notice goes to
stderr so stdout stays pure JSON. Regression test: `test_json_output_serializes_artifacts_as_objects`.

**D-061 License: Apache-2.0, confirmed by the owner.** At the release gate the
owner confirmed Apache-2.0 as intentional. This resolves D-001. `LICENSE` is the
full Apache-2.0 text. `pyproject.toml` uses the SPDX form
`license = "Apache-2.0"` with `license-files` under `[project]`, which needs
setuptools 77 or newer, so the build requirement is `setuptools>=77`. This removes
the deprecation warnings from the earlier build. `NOTICE` keeps the copyright
line and the MIT attribution for the adapted Agent Reach code. The owner decision
is recorded in D-051.

**D-062 Clean installs include the lint and type tools.** The `dev` extra had only
pytest. A fresh `./scripts/setup.sh` therefore could not run the documented
`ruff` and `mypy` commands. The extra now lists `ruff` and `mypy`. Verified: a
fresh clone plus setup.sh gives working ruff and mypy.

**D-063 Security scan: triage and one hardening change.** Bandit on
`halfiralens/` (`-lll`) reported one High finding: `extractall` in the engine
installer. The tar path already uses `filter="data"`. The zip path now validates
every member name (`check_archive_member`) before extraction, and rejects
absolute, traversing, or drive-qualified names. The remaining zip call carries
`# nosec B202` with the reason. The five Medium findings are `urlopen` calls
(`B310`). Each target is checked: web reads go through `normalize_public_http_url`
(http and https only), V2EX is host- and scheme-checked, and the GitHub and
opencli targets are constants. pip-audit reports no known vulnerabilities in the
dependency set. Not done: the engine download has no checksum verification,
because the release does not publish one that was checked here (KNOWN_LIMITATIONS).

**D-064 Real-data verification uses public GitHub operations.** The sandbox can
reach api.github.com and github.com, but not web search engines or the web reader.
Public web research was therefore run as far as the network allowed: the CLI
returned `stop_reason: search_failed` with zero sources, and the report says so.
To exercise the research code on real content, `scripts/live_github_research.py`
feeds the real planner with the public `fetch("github", "search_repos")` and
`fetch("github", "readme")` operations. Everything after retrieval is the real
research code, and the run is replayed offline to confirm the report reproduces.
This is not a public-web test, and the docs do not describe it as one.

**D-065 Keyword queries for the GitHub corpus.** GitHub repository search requires
every term to match. A long natural-language question returns no repositories
(verified: "headless browser AI agents" returned 2, "headless browser automation AI
agents" returned 0). The planner does not split questions, so live GitHub runs
need keyword questions. Recorded as a limitation; not changed in the planner.

**D-066 Merge policy.** PR #1 is merged into `main` only after the gates pass. The
merge is a GitHub merge of the session branch, not a direct push to `main`, and
it is a merge commit. No force-push, no history rewrite, and the branch is kept.

## Final production remediation

**D-067 Free engines first in the default chain.** The default engine order
was `semantic-search, duckduckgo, bing`, so every search first attempted the
optional bridge that needs user configuration (`mcporter` + Exa). The promise
is that search works with no separately configured paid API key, so the order
is now `duckduckgo, bing, semantic-search`. The bridge still runs when the
free engines fail or more results are needed; nothing that worked before was
removed.

**D-068 Query hygiene before any engine.** Queries are stripped of control and
invisible characters (bidi overrides can make a query display differently from
what is sent), whitespace-normalized, and capped at `search_max_query_chars`
(400). Violations raise `FilterError` (`invalid_input` over MCP). Tokenization
is symbol-aware (`halfiralens/search/querytext.py`): `c++`, `c#`, `.net`,
`f#` survive as single tokens, quoted phrases are kept intact through
reformulation, and CJK runs are additionally split into character bigrams so
2-character terms match longer compounds.

**D-069 Concurrent query variants with serialized navigation.** Reformulated
query variants now run in a bounded thread pool (`search_parallel_queries`,
default on; workers capped by `search_max_concurrent`). Browser-backed engines
serialize navigate+read on a process-wide lock in `fetch_rendered_html`
because the shared engine has one current page; CLI-bridge backends run truly
in parallel. Results merge in query order, so output is deterministic;
sequential mode keeps its early-stop when enough unique URLs are found.

**D-070 Atomic, honest engine installation.** `install_engine` now downloads
with retries (transient only), optionally verifies a supplied SHA-256
(`--checksum` / `HIL_ENGINE_SHA256`), extracts and startup-probes the binary
inside a temporary directory, and only then moves it into place. A failed
install never overwrites or deletes a working engine. Upstream publishes no
checksum asset (verified against the GitHub API for v0.2.4), so without a
supplied value the report says `checksum_verified: false` — it never claims a
verification that did not happen. `install-engine --status` reports the
current state without installing. Also fixed: `tarfile.extractall(filter=...)`
does not exist before Python 3.12; the installer now validates tar member
names itself and falls back where `filter` is unavailable (requires-python is
3.10).

**D-071 Reader hardening and consistent max_chars.** `read_with_static_reader`
fetches through `safe_urlopen` (D-072), streams under an overall deadline
(`read_total_timeout_seconds`, slow-drip protection), enforces the size cap
(`read_max_bytes`), classifies HTTP status (404 / 403 bot-check / 429 / 5xx /
redirect loop), and detects soft 404s (short body + not-found markers).
`max_chars` is enforced on both read backends — it was previously ignored on
the static path — and artifacts report `truncated` and the budget in metadata.
The CLI `read` command and the MCP `read` tool accept `max_chars`. The cache
stores the full text; truncation happens when serving.

**D-072 Redirect-validated, DNS-pinned fetches.** `security.safe_urlopen`
closes KNOWN_LIMITATIONS items 17/36 for the reader path: hostnames are
resolved and every answer must be global (mixed public+private answers are
refused as a rebinding signature), the connection goes straight to the
validated address (no rebinding window), every redirect hop is re-validated
against the public-URL policy, https→http downgrades are refused, and chains
longer than 5 hops are stopped. Verified offline with a local server and
simulated DNS; see `tests/test_ssrf_fetch.py`.

**D-073 Honest no-results, summary, and demotion.** `no_results_reason`
distinguishes `filtered_out` (hits existed but were removed by the URL guard
or filters) from `no_results` (engines answered empty), and only reports
`engines_failed` when no engine answered at all. `SearchResponse.summary`
adds one honest sentence. Groups whose hits carry a `prompt_injection:*` flag
get their relevance factor halved: flagged results are demoted, never silently
dropped, and the flag travels with them.

**D-074 Verification honesty.** `scripts/verify_interfaces.py` reports
PASS / FAIL / BLOCKED; live checks the environment cannot reach are BLOCKED and
listed as unverified in the summary, and the exit code reflects only real
failures. The ranking benchmark in `scripts/ranking_benchmark.py` is a clearly
labeled REPLACEMENT for the unrecoverable original 20-case audit benchmark
(single-commit history; no benchmark artifact exists anywhere in the repo).
Measured delta, new relevance vs the verbatim legacy scorer: MRR +0.1000,
nDCG@5 +0.0738 over 20 synthetic cases.
