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
 ├── search/            schema, reformulation, fallback, filters, dedup, ranking,
 │                      keyless HTTP engines, optional SearXNG / Exa / paid adapters
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
  mcp_server.py    IraLens MCP server (49 tools at v0.2.1)
  engine/          spawns the engine binary, classifies its errors, exports state
  sources/         platform modules behind the Source base class
```

## Unified data model

Every retrieval is an `Artifact`:

`title, url, source, kind, content, content_format, metadata
(source-specific, preserved), retrieval_method (direct / browser /
static-reader / api:… / cli:…), retrieved_at, discovered_from` — provenance:
the search query or page that surfaced the item. Content is always flagged
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
