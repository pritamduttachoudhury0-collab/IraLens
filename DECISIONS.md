# Design decisions — IraLens

Why things are the way they are. Each note records a choice, the reason, and
the trade-off. Ordered by subsystem, oldest themes first. For what is
verified versus unverified, see `KNOWN_LIMITATIONS.md` and `BENCHMARKS.md`.

## Search

**Keep the legacy surface; add a structured surface beside it.**
`IraLens.search()` returns `List[Artifact]` as it always has. `search_api()`
returns a typed `SearchResponse` with per-engine outcomes, filters, dedup log,
and scores. Existing callers keep working; agents get honest structure.

**Legacy `backend` names are aliases.** `semantic-search` maps to the Exa
bridge; `browser-search` maps to the DuckDuckGo-then-Bing chain. New engine
names are `duckduckgo`, `bing`, `wikipedia`, and `searxng`.

**Empty results return `[]` instead of raising.** When every engine answered
and none had hits, the answer is honestly "nothing found". When engines
*failed*, `search()` raises and `search_api()` reports
`no_results_reason == "engines_failed"`. "No results" and "blocked" must be
distinguishable.

**Failure taxonomy.** Every engine attempt is classified: `ok`, `empty`,
`captcha`, `rate_limited`, `layout_changed`, `transient`, `unavailable`,
`circuit_open`. Captcha and rate-limit signals are never retried; transient
failures retry with exponential backoff, capped by `Settings`.

**Fallback and cross-engine agreement.** The chain runs until
`search_min_engines` engines return hits (default 2). Hits confirmed by a
second engine get an agreement bonus; an empty or failed engine triggers a
recorded fallback. With a single engine the agreement factor is always 1.0
and uninformative — the `score_breakdown` shows this rather than hiding it.

**Filters are native, emulated, post-filtered, or unsupported. Never silent.**
Each engine declares a mode per filter and the response reports it.
`include_domains` and `file_type` are emulated with `site:`/`filetype:`
operators on DuckDuckGo and Bing, and post-filtered on the Exa bridge.
Undated results are kept and counted (`date_unknown_kept`). Filters an engine
cannot apply are reported `unsupported`, never faked.

**Deterministic reformulation.** Query variants are produced by fixed
strategies (original, keywords, sub-question, synonym), capped at
`reformulate_max_queries`, each tagged with its strategy so results are
traceable. An LLM expander can be plugged in; it is not the default.

**Dedup in two passes.** Pass 1 merges canonical URL matches (tracking
params, fragments, `www.`, trailing slash removed; path case kept). Pass 2
merges near-duplicate titles (token Jaccard ≥ 0.85) on the same registrable
host only, so distinctly versioned pages (`/3.10/` vs `/3.12/`) are never
merged. Every merge is logged with its reason.

**Ranking is explainable.** Score = weighted mean of relevance, authority,
freshness, and agreement; weights, half-life, and the authority table come
from `Settings`, and each factor appears in `score_breakdown`. The authority
table is a coarse domain-suffix heuristic (`.gov` 0.9, `.edu` 0.85,
`.org` 0.6, default 0.5), documented as such — not a quality measure.

**Engines fetch over HTTP; parsing is pure.** The free engines fetch results
pages directly over SSRF-safe HTTP (`iralens/fetch.py` — DNS-pinned,
redirect-validated, size-capped, deadline-bounded). Parsing is a pure
function of the payload, tested against saved fixtures, so markup drift
surfaces as `layout_changed`, never as silently wrong results. The browser
engine is a fallback for bot walls and JavaScript pages, not the transport.

**Free engines first.** The default chain is DuckDuckGo → Bing → Wikimedia's
official API → the semantic-search bridge (when installed). Working search
must not require a separately configured paid key. A Brave, Serper, or Tavily
key configured by the operator enables one explicitly optional paid emergency
fallback after the free sources fail; it is otherwise never called.

**Query hygiene before any engine.** Queries are stripped of control and
invisible characters (bidi overrides can change what a query displays as),
whitespace-normalized, and capped at 400 characters. Tokenization is
symbol-aware: `c++`, `c#`, `.net`, `f#` survive as single tokens, quoted
phrases stay intact through reformulation, and CJK runs are additionally
split into character bigrams so two-character terms match longer compounds.

**Concurrent variants, serialized navigation.** Reformulated variants run in
a bounded thread pool; browser-backed engines serialize navigate+read on a
process-wide lock because the shared engine has one current page. Results
merge in query order, so output is deterministic. Sequential mode keeps its
early-stop when enough unique URLs are found.

**SearXNG only when explicitly configured.** No public SearXNG instances are
bundled as defaults: their owners' availability, privacy practices, terms,
and upstream mix vary, and unverified third parties should not sit in the
default path. A configured (typically self-hosted) instance is inserted into
the chain, with per-instance cooldowns and rotation. Self-hosting does not
remove the upstream engines' terms or rate limits.

**Wikimedia as the official supplemental API.** The MediaWiki `list=search`
API runs with an identifying User-Agent and at least one second between
requests in a process. It is narrow but contract-clear; it is not a general
web index.

**Twenty-way concurrency with politeness.** The global search gate allows 20
concurrent searches (`search_max_concurrent`); a per-site gate
(`search_per_site_concurrent`, default 3) keeps simultaneous connections to
any single provider polite, and a 300-second answer cache serves repeated
queries before any engine slot is taken. Burst capacity and upstream
politeness are both first-class requirements.

**Settings, not magic numbers.** Every limit, weight, and TTL lives in
`iralens/settings.py`, readable from `~/.iralens/config.yaml` or
`IRALENS_*` environment variables.

## Reliability and caching

**Shared failure taxonomy across layers.** Search, reading, and the optional
backends use the same classification so callers see one vocabulary.

**Durable circuit backoff.** Engine circuits and per-instance cooldowns
persist in a local SQLite store (`IRALENS_HOME/state/search-health.sqlite3`),
survive restarts, and coordinate local processes: CAPTCHA/429 opens a
cooldown immediately, repeat failures extend it exponentially, and a
transaction permits only one half-open probe. The store holds engine/host
labels and counters — never queries, content, or keys — and fails open if
the state directory is unavailable. It is not a distributed health service.

**Degraded searches are never cached.** A failed or skipped engine means the
response is not stored; an empty-but-healthy response is not stored either
(a transient block must not stick for 15 minutes). Only complete, successful
responses enter the cache.

**Page cache covers static reads only.** Cached page fetches are public,
static reads with a one-hour TTL. Browser reads are never cached because the
browser can hold session state.

**Exit-code contract.** The CLI guarantees: 0 = success *including an honest
zero-results answer*; 2 = caller error (bad input, bad URL, forbidden
target); 3 = internal error; 4 = every search engine failed (the JSON output
still explains why); 130 = interrupted. Agents can branch on it.

## Reading

**Local-first reading.** Reads are direct fetch plus standard-library
extraction by default. The remote reader (`r.jina.ai`) is off by default and
opt-in (`IRALENS_READ_REMOTE_READER_ENABLED=1`), used only after the direct
read fails; URLs sent to it are shared with that third party. Chain: direct →
remote reader (opt-in) → local browser.

**Local extraction is honestly lossy.** The stdlib extractor has no DOM,
CSS layout, or JavaScript: complex widgets disappear and tables become pipe
rows. `mode=browser` exists for fidelity, and the docs say which mode to use
when.

**Hardened fetch path with consistent budgets.** Reads stream under an
overall deadline (slow-drip protection), a 5 MB response cap (20 MB for
PDFs), HTTP status classification (404 / bot-check 403 / 429 / 5xx / redirect
loops), and soft-404 detection. `max_chars` is enforced on every backend and
`truncated` is reported in metadata; the cache stores full text and truncates
when serving.

**Redirect-validated, DNS-pinned fetches.** Hostnames are resolved up front
and every answer must be global (mixed public+private answers are refused as
a rebinding signature); the connection is pinned to the validated address;
every redirect hop is re-checked against the public-URL policy; https→http
downgrades and chains over 5 hops are refused.

**JS-shell pages fall back to the browser automatically.** In `mode="auto"`
a thin or JavaScript-shell static result triggers one rendered read; the
static text is kept when rendering is not better or the engine is
unavailable, and provenance records which path produced the artifact. When
both fail, the *primary* static error is raised with the fallback failure
attached as a hint — the real cause must not be hidden.

**PDFs are parsed, not decoded as noise.** PDF bodies are detected by magic
bytes and extracted with `pypdf`. Missing dependency → typed
`pdf_support_missing`; encrypted → `pdf_encrypted`; image-only →
`pdf_no_text`; corrupt → `pdf_parse_failed`. One unreadable page never loses
the document. Extracted text is normalized for ligatures, soft hyphens,
BOMs, and line-wrap hyphenation; when `pypdf` returns sparse text an
*installed* `pdftotext` retries locally before OCR.

**Scanned PDFs get local OCR with honest status.** When `pdftoppm` and
`tesseract` are installed, image-only PDFs are OCRed locally by default —
bounded by subprocess deadlines and a page cap, never uploaded anywhere.
Without the tools, a scan fails with typed `pdf_ocr_support_missing` and
`iralens doctor` names the missing tools. No cloud OCR is used; opt out with
`IRALENS_PDF_OCR_ENABLED=false`.

## Security

**Content guard flags, never rewrites.** Page content is returned as fetched
with advisory flags (`prompt_injection:*`, invisible characters) attached to
provenance. Flagged search hits are demoted in ranking — never silently
dropped. Only short search titles/snippets get invisible-character
stripping because they render into result lists.

**Non-public URLs are dropped from results.** Every hit passes
`normalize_public_http_url` before ranking; a hostile results page cannot
route the agent at internal services. Fetch-time DNS and redirect validation
close the rebinding window on the read path.

**Private search backends need an explicit allowlist.** A self-hosted SearXNG
on a LAN address is legitimate but private targets stay blocked unless
`IRALENS_ALLOWED_PRIVATE_HOSTS` names them — and even then only for search
transports, never page reads; metadata endpoints and link-local names stay
blocked regardless.

**Credentialed requests never cross origins.** Provider keys travel in
headers; requests carrying credentials use a same-origin redirect policy so a
redirect cannot forward a key or query to a different host.

**Public API tokens have a floor.** The HTTP API tolerates unauthenticated
loopback development, but a non-loopback bind requires a token of at least
32 characters so the service cannot be exposed with a trivial password.
Operators still need TLS, high-entropy keys, and network controls.

## Browser engine

**Internally vendored browser subsystem, zero runtime binary downloads.**
Core Obscura crates (`obscura-ssrf`, `obscura-dom`, `obscura-net`,
`obscura-js`, `obscura-browser`, `obscura-mcp`, `obscura-cli`, `obscura` at
commit `bd9529418b7fa975b50eabeac415cfaaa865a588`, Apache-2.0) are vendored
directly inside `iralens/_internal/browser_engine/vendor/obscura`. Rather
than requiring a separate download from GitHub Releases at runtime,
`iralens._internal.browser_engine` executes the vendored `bootstrap.js` +
`indexed_db.js` V8 runtime inside `py_mini_racer`'s prebuilt `libmini_racer`
V8 isolate (with automatic fallback to a persistent Node.js V8 VM worker if
needed), paired with Obscura's SSRF guard, RFC 6265 cookie jar, and MCP
JSON-RPC 2.0 server (`iralens-browser-engine`). When a Rust toolchain is
present and `IRALENS_BUILD_RUST_ENGINE=1` is set, the vendored Rust workspace
can also be compiled directly from source.

**Atomic, honest installation.** `find_engine()` discovers the bundled
`iralens-browser-engine` out of the box after `pip install`. Running
`iralens install-engine` materializes the engine from the vendored package
into `~/.cache/iralens/engine/` (or downloads if `IRALENS_FORCE_REMOTE_DOWNLOAD=1`
is set), optionally verifies a caller-supplied SHA-256, startup-probes the
binary in a staging directory next to the destination (so placement is a
same-filesystem rename), and only then swaps it in. A failed install can
never destroy a working engine.

**A dead engine fails in about a second.** The driver polls the child process
while waiting on RPC and raises a typed `browser_engine_unavailable` as soon
as the process is gone, remembers the failure for the process lifetime, and
gives the `initialize` handshake its own 20-second budget. A dangling engine
shim must not cost minutes per call.

**Engine configuration is conservative.** The integrated browser engine runs
with stealth features OFF unless explicitly enabled, enforces SSRF validation
on every navigation and redirect hop, bounds V8 script execution (15 s
timeout, 256 MB heap cap), and communicates over stdio pipes only.

## Research

**Deterministic core; LLM is an optional adapter.** Planning, claim
extraction, contradiction detection, synthesis, and the confidence formula
are deterministic and inspectable. `research/llm.py` accepts any
`complete(prompt)` callable, but no model ships with IraLens and none is a
hidden dependency of the core result.

**Replayable trace.** The trace records every search response, expander
decision, page read, and the date used; `ResearchPlanner.replay` rebuilds the
report from it with no network access. Research output must be auditable
after the fact.

**Explicit stop reasons.** `coverage_reached`, `budget_exhausted`,
`no_new_sources`, `search_failed`, `max_rounds`. `search_failed` is separate
so "every engine was blocked" is not reported as "nothing new to find", and
it exits 4 per the CLI contract.

**Claims are sentences; contradictions are candidates.** Sentences sharing a
term with the question become claims — there is no entailment check.
Numeric and negation mismatches become contradiction *candidates* labeled
`heuristic`, never verdicts; four-digit years are excluded from number
comparison so differing years do not masquerade as conflicting measurements.

**One scoring path for statements.** An LLM may propose wording and grouping,
but `make_statement` computes status and confidence either way, and a
proposal citing an unknown claim id is rejected as a whole.

**Research reads static pages.** The planner does not drive the browser:
reads stay cacheable and free of session state, at the cost of JS-heavy
pages staying snippet-only when no engine is installed.

**Confidence and authority are transparent heuristics.** The formulas and
constants live in `Settings` and `research/synthesis.py`. They are not
calibrated against ground truth, and the docs say so.

## Interface and packaging

**One facade, three surfaces.** Python (`IraLens`), CLI (`iralens`), and MCP
stdio server share one data model (`Artifact`), one session, and one error
taxonomy. MCP errors distinguish caller mistakes (`invalid_input`) from
internal failures.

**JSON output is real JSON.** Every command accepts `--json` before or after
the subcommand; results serialize through `to_dict()` (never Python reprs),
and untrusted-content notices go to stderr so stdout stays parseable.

**Verification reports are honest.** `scripts/verify_interfaces.py` prints
PASS / FAIL / BLOCKED: checks the network cannot reach are BLOCKED and listed
as unverified, and the exit code reflects only real failures.

**The ranking benchmark is a labeled replacement.** The original 20-case
audit benchmark was never committed to the repository, so
`scripts/ranking_benchmark.py` measures the current scorer against a verbatim
copy of the legacy scorer on 20 synthetic cases with explicit relevance
judgments. Its numbers are improvements over the legacy scorer on that
synthetic set — not results from the lost original. Measured: MRR 0.875 →
0.975 (+0.100), nDCG@5 0.908 → 0.982 (+0.074) at introduction. After the
2026-10-17 evidence-driven scorer changes, the same synthetic harness measured
MRR 0.925 and nDCG@5 0.945 (still above legacy 0.875 / 0.908); live search
quality is not measured here. The previous figures remain historical, and the
one case both scorers fail is recorded as a limitation.

**The acceptance benchmark is frozen.** `benchmarks/` pins 200 labeled
queries and 100 labeled URLs plus thresholds (`FREEZE.sha256`). The runner
bypasses the cache, paces requests realistically, appends a row to
`BENCHMARKS.md` only when every threshold passes, and labels anything it
could not run as `not_run` with the reason. Corpus or thresholds are never
edited to raise a score.

**A daily watch is a canary, not an acceptance test.** The scheduled workflow
makes two low-volume public page reads per day, bypasses the cache, and
uploads per-run diagnostics. It monitors that reading keeps working; it does
not replace the frozen benchmark.

**The HTTP API is a single node.** Standard-library server, bounded workers,
native and Serper-compatible endpoints, authenticated metrics, mandatory
token for non-loopback binds. No hosted URL, SLA, multi-node state, or TLS
termination is claimed.

**Setup is honest about platform coverage.** `scripts/setup.sh` is tested on
Linux; `scripts/setup.ps1` is provided for Windows and labeled untested;
Docker files are provided and must be built on the target host before being
relied on.

## Licensing

**Apache-2.0.** The project is Apache-2.0: `LICENSE` holds the full text,
`pyproject.toml` uses the SPDX form, and `NOTICE` carries the copyright line
plus the MIT attribution for the open-source components adapted into the
source layer, as their licenses require. The component inventory is in
`docs/audits/license-audit.md`.

## Scope

**What IraLens is not.** Not an independently crawled search index, not a
managed service, not a fact-checker. The research layer produces leads with
stated limits. The scope boundary lives in the README and
`KNOWN_LIMITATIONS.md`; deliberate non-builds (LLM-by-default, distributed
health store, public SearXNG defaults, hosted endpoint) are recorded in the
notes above.
