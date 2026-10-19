# IraLens architecture

IraLens is a single, unified Internet-access system: web search, page
reading, full browser automation, and specialized platform access behind one
facade, plus a deterministic research layer on top. This document describes
the system as it is today. Design rationale lives in `DECISIONS.md`; current
verification status lives in `KNOWN_LIMITATIONS.md` and `BENCHMARKS.md`.

```
AI agent
   ↓
IRALENS                 ← one interface: CLI · Python API · MCP stdio server
   ↓
Unified Internet capabilities
 ├── web search          ├── specialized source access
 ├── page reading        ├── persistent session state
 ├── full browser        └── unified data & provenance
```

## Layers

```
IraLens facade (core.py) — search(), search_api(), research(), open(), read(), fetch(), …
 ├── search/            schema, adaptive reformulation, fallback, filters, dedup, ranking,
 │                      keyless SERPs, intent-routed Crossref/arXiv/Stack Exchange/GitHub APIs,
 │                      optional SearXNG / Exa / paid adapters
 ├── reliability.py     failure taxonomy, retries, persistent circuit state, concurrency gates
 ├── cache.py           TTL disk cache (search responses, static reads)
 ├── content_guard.py   injection / invisible-character advisory flags
 ├── api.py             optional single-node HTTP API and Prometheus metrics
 ├── settings.py        every tunable (config.yaml or IRALENS_* env)
 ├── engine/            browser-engine driver (JSON-RPC over stdio, installer, locator)
 ├── sources/           specialized sources (github, youtube, rss, v2ex, reddit, …)
 └── research/          planner loop, evidence, contradiction candidates, synthesis,
                        provenance graph, replay, optional LLM adapter
```

Data flow for `research()`: question → query expansion → `search_api` per
query → top sources read locally → claims → contradiction candidates → cited
statements → provenance graph and replayable trace. The pipeline is
deterministic unless an optional LLM adapter is supplied; its conclusions are
heuristic leads, not verdicts.

## Module map

```
iralens/
  core.py          IraLens facade — the single entry point
  model.py         Artifact: unified data model + provenance
  errors.py        unified error taxonomy
  security.py      SSRF guard, redirect-validated DNS-pinned fetch, credential scrubbing
  session.py       unified session: history, discovered URLs, persisted engine state
  config.py        ~/.iralens/config.yaml (atomic, owner-only, symlink-safe)
  proc.py          subprocess runner + honest health probing
  cli.py           `iralens` command
  api.py           `iralens-api` / `iralens serve` HTTP service
  mcp_server.py    IraLens MCP server (49 tools at v0.3.1)
  engine/          spawns the engine binary, classifies its errors, exports state
  _internal/
    browser_engine/
      vendor/obscura/        vendored Obscura source tree (rev bd9529418b7f, Apache-2.0)
      runtime.py             V8 isolate (py_mini_racer / node VM) + ObscuraNativeEngine
      host_ops.js            HTML5 DOM tree, CSS selector engine & Deno.core.ops bridge
      host_mcp.js            document load lifecycle & 36 MCP browser_* tool implementations
      ssrf.py                IPv4/IPv6 SSRF deny-set & per-hop redirect validator
      cookies.py             RFC 6265 CookieJar with HttpOnly/Secure/SameSite & 0600 persistence
      mcp_server.py          JSON-RPC 2.0 stdio server & CLI entrypoint (iralens-browser-engine)
      builder.py             relocatable bundled launcher, generated runtime launcher,
                             optional Rust workspace builder
      bin/iralens-browser-engine
                             relocatable launcher shipped as package data; read-only at
                             runtime and free of any absolute path
      UPSTREAM_PROVENANCE.md upstream revision, imported crates, modifications & update procedure
      THIRD_PARTY_NOTICES.md Apache-2.0 and transitive dependency notices
  sources/         platform modules behind the Source base class
```

## Integrated native browser subsystem

IraLens embeds Obscura (`https://github.com/h4ckf0r0day/obscura`, commit
`bd9529418b7fa975b50eabeac415cfaaa865a588`, base tag `v0.2.4` at
`1fccab2c1ecd0f7eadc16d7a520ba227f9e7f782`, Apache-2.0) as an internally
maintained browser subsystem under `iralens/_internal/browser_engine/`:

- **Vendored components:** `obscura-ssrf`, `obscura-dom`, `obscura-net`,
  `obscura-js` (`js/bootstrap.js`, `js/indexed_db.js`, `src/markdown.rs`),
  `obscura-browser`, `obscura-mcp`, `obscura-cli`, and `obscura` (4.1 MB
  uncompressed; 1.2 MB wheel / 1.3 MB sdist). `obscura-render` (`wgpu` GPU
  rasterizer) and `obscura-cdp` are intentionally excluded to keep the
  distribution lightweight.
- **Zero-download bundled execution:** On standard `pip install`,
  `iralens-browser-engine` runs the unmodified vendored Obscura V8 runtime
  (`bootstrap.js` + `indexed_db.js` + `HTML_TO_MARKDOWN_JS`) inside an isolated
  `py_mini_racer` (`libmini_racer`) V8 isolate (with automatic fallback to a
  persistent Node.js V8 VM worker if `py_mini_racer` is unavailable).
  `find_engine()` discovers the bundled engine automatically without runtime
  binary downloads from GitHub Releases.
- **No runtime writes to package data:** the in-package launcher is
  relocatable — it resolves its interpreter (`$IRALENS_PYTHON`, then
  `python3`/`python`) when it runs, so it carries no build-time path. Setup,
  `doctor` and discovery only ever read it (see
  `builder.ensure_bundled_launcher`). A launcher bound to a specific
  interpreter is generated on demand into the writable per-user engine cache
  (`engine_cache_dir()`, or `$IRALENS_ENGINE_CACHE_DIR`) by
  `builder.ensure_generated_launcher`; when that directory cannot be written
  the call reports unavailable rather than returning a stale launcher. A
  checkout therefore stays clean after `scripts/setup.sh` and `iralens doctor`.
- **Optional Rust compilation:** When `cargo` is present and
  `IRALENS_BUILD_RUST_ENGINE=1` is set, `builder.py` compiles the vendored
  Rust workspace (`vendor/obscura/Cargo.toml`) directly from source.
- **Process & resource boundaries:** `BrowserEngine` (`iralens/engine/native.py`)
  manages `iralens-browser-engine mcp` over stdio JSON-RPC 2.0 with a bounded
  startup handshake (default 20 s), per-call RPC timeouts, V8 script execution
  timeout (default 15 s) and heap cap (256 MB), 10 MB navigation body cap,
  20-hop redirect limit with per-hop SSRF re-validation, and deterministic
  subprocess termination on `IraLens.close()`.

## Unified data model

Every retrieval is an `Artifact`:

`title, url, requested_url, final_url, status_code, content_type, warnings,
errors, source, kind, content, content_format, metadata (source-specific,
preserved), retrieval_method (direct / browser / static-reader / api:… /
cli:…), retrieved_at, discovered_from, provenance` — provenance records the
search query or page that surfaced the item, redirect status, and security
flags without exposing internal component names. Content is always flagged
untrusted.

## Unified errors

`PageUnavailableError, NavigationError, SourceUnavailableError,
AuthRequiredError, OperationUnsupportedError, OperationTimeoutError,
ExtractionError, SecurityBlockedError, EngineUnavailableError,
SessionStateError, InvalidInputError` — each carries a user-safe `message`
(scrubbed of credentials) plus an optional developer `detail`. The CLI maps
them onto the documented exit-code contract.

## Unified session

One session across all capabilities: navigation history, discovered URLs
with provenance, and the browser engine's exported storage state (cookies +
web storage) persisted under `~/.iralens/state/`, so browser logins survive
restarts and every surface sees the same bookkeeping.

## Unified interface

- **Python:** the `IraLens` facade — `search()`, `search_api()`, `open()`,
  `read()`, `research()`, all browser operations, `fetch(source, op, …)`,
  `sources()`, `doctor()`, `session()`, `close()`.
- **CLI:** `iralens <command>` mirrors the facade; every command supports
  `--json`.
- **MCP:** `iralens mcp` — one stdio server named `iralens` whose tools map
  one-to-one onto the capability surface.
- **HTTP:** `iralens-api` — native `/v1/search`, Serper-shaped `/search`,
  `/v1/read`, health and authenticated metrics for serving remote agents.

## Security posture

- Every URL passes the SSRF guard before any fetch (private/internal hosts,
  credentials-in-URL, non-HTTP schemes rejected); the engine enforces a
  second guard.
- The read path fetches through redirect-validated, DNS-pinned connections
  with size caps and deadlines.
- Internet content is returned as *untrusted data* (flagged in artifacts and
  MCP output); advisory content-guard flags travel with it, and flagged
  results are demoted, never silently dropped.
- Credentials live in an owner-only config file, are injected into child
  processes only, and are scrubbed from all error text.
- No auto-login: authenticated sources use credentials you provide or your
  own browser sessions.

## Deliberate non-goals

IraLens does not operate an independently crawled search index or vector
database, a hosted service with uptime promises, or a managed scheduler.
The research layer provides evidence leads with stated limits, not
fact-checking verdicts.
