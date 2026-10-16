# IraLens — Fusion Architecture

IraLens is a single, unified Internet-access system. This document records
the study of the two implementation sources and how their capabilities were
consolidated. **The two sources are implementation details; the AI-facing
system is IraLens only.** Historical component observations in this document
do not imply current live-provider, browser-binary, Docker, or deployment
verification; see `docs/PHASE_STATUS.md` and `docs/CAPABILITY_MAP.md` for the
current evidence boundary.

```
AI
 ↓
IRALENS               ← one interface: CLI + Python API + MCP server
 ↓
Unified Internet capabilities
 ├── web search        ├── specialized source access
 ├── page reading      ├── persistent session state
 ├── full browser      └── unified data & provenance
```

---

## 1. Study of the implementation sources

### Source A — capability layer for specialized platform access
(Python, MIT, `sources/agent-reach/`)

| Aspect | Finding |
|---|---|
| Architecture | Installer/doctor scaffolding + a registry of 16 platform *channels*. Channels health-check upstream CLIs; agents were expected to call those CLIs directly via skill docs. |
| Channels | github (gh CLI), twitter (twitter-cli/OpenCLI/bird), youtube (yt-dlp), bilibili (bili-cli/OpenCLI/API), reddit (OpenCLI/rdt-cli), facebook & instagram (OpenCLI), xiaohongshu (OpenCLI/xiaohongshu-mcp/xhs-cli), linkedin (mcporter MCP), boss (boss-agent-cli over CDP), xueqiu (OpenCLI/cookies), xiaoyuzhou (ffmpeg + Whisper), v2ex (public JSON API), rss (feedparser), exa_search (mcporter + Exa MCP), web (Jina Reader, with a real `read()` implementation). |
| Reusable mechanisms | Ordered multi-backend routing per channel (`backends[0]` preferred, health probing sets `active_backend`); real command probing that distinguishes missing/broken/timeout (not just `which()`); SSRF-safe URL normalization; URL-credential scrubbing; symlink-safe private config (`config.yaml`, atomic owner-only writes); child-process-only credential injection (never mutates `os.environ`); anti-bot challenge detection on reader output; Jina Reader HTTP reading with response caps. |
| Runtime read/search code | Only `WebChannel.read()` (Jina) is real runtime code; everything else was documentation + health checks. No browser, no unified data model, no session state, no unified errors. |
| Interfaces | `agent-reach` CLI (setup/install/doctor/configure/transcribe…), MCP server exposing only `get_status`. |

### Source B — headless browser engine
(Rust, Apache-2.0, `sources/obscura/`; prior integration notes refer to v0.2.4,
but no browser binary/live run is verified in the current workspace)

| Aspect | Finding |
|---|---|
| Architecture | Independent headless browser engine: crates for JS (V8), DOM, networking, native rendering, CDP server, MCP server, CLI, SSRF guard. |
| Capabilities | ~40 browser primitives: navigate/back/forward/reload, snapshot, markdown, links, interactive elements, click, fill, type, press key, select option, scroll, evaluate JS, wait for selector/text, in-page text search, form detect/fill, structured CSS extraction, count, attribute read, tabs (new/list/switch/close), cookies get/set/clear, storage-state export/import, network log, console log, screenshot (PNG), PDF export, close. Also one-shot `fetch` and parallel `scrape` modes. |
| Session model | Persistent page across calls, real tab isolation, cookie jar, exportable/importable storage state (cookies + localStorage + sessionStorage), stealth mode, robots.txt obedience, SSRF guard, file:// navigation blocked. |
| Interfaces | Native MCP server over stdio/HTTP (line-delimited JSON-RPC), CDP endpoint, CLI. |
| Gaps | No web search, no specialized platform access, no unified content/provenance model, no cross-source routing. |

### Overlap & complementarity

| Area | A | B | Resolution in IraLens |
|---|---|---|---|
| Web page reading | Jina Reader (remote markdown service) | Engine rendering (local, full JS) | **One `read/open` capability**: specialized source → local direct fetch/extraction → optional local browser for JS-shell failures. The remote Jina reader is a separate, explicit opt-in fallback. |
| Web search | Exa via mcporter | (none — only in-page text find) | **One `search` capability**: configured keyless HTTP backends (DuckDuckGo/Bing/Wikimedia), optional private SearXNG, optional bridge and explicit paid backup. These sources are not an independent IraLens index; live reliability is unverified here. |
| Reddit/WeChat-style JS-walled content | Auth-only CLIs | Engine rendering | Sources may use the **same engine** as one of their backends (e.g. reddit page read falls through to browser). |
| SSRF protection | Python URL normalization | Rust `obscura-ssrf` crate | Both kept: facade normalizes every URL; engine enforces its own guard. |
| Cookies/auth | Cookie import helpers, child env credential injection | Cookie jar + storage state export/import | Unified session: engine holds live cookies; storage state persisted by IraLens across runs; source CLIs get credentials via child env only. |
| Health/doctor | Channel probing framework | — | Generalized to all capabilities (engine + sources) in `capabilities.py`. |
| Identity/CLI/MCP | `agent-reach` CLI, `get_status` MCP | `obscura` CLI, `browser_*` MCP | **Removed as AI-facing surfaces.** Replaced by one IraLens CLI, Python API, and MCP server. |

### Components removed (conflicted with unified architecture)

- Both original CLI/MCP identities and the "agent calls upstream CLIs directly via skill docs" pattern → replaced by native IraLens operations.
- Duplicate web-reading paths (reader vs. browser as separate products) → consolidated into one backend chain.
- Agent Reach's installer/setup wizard scope (system-package installation) → out of scope; IraLens probes what exists and degrades gracefully.
- The registry preserves the documented channel surface, but it does not
  guarantee that every external CLI, credentialed source, provider, or live
  workflow is installed, authorized, or available. See the capability map's
  per-source verification status and `KNOWN_LIMITATIONS.md`.

---

## 2. IraLens structure

```
iralens/
  core.py          IraLens facade — the single entry point
  model.py         Artifact: unified data model + provenance
  errors.py        unified error taxonomy
  security.py      SSRF guard, credential scrubbing, untrusted-content rules
  session.py       unified session: history, discovered URLs, persisted engine state
  config.py        ~/.iralens/config.yaml (atomic, owner-only, symlink-safe)
  proc.py          subprocess runner + honest health probing
  capabilities.py  one doctor for the whole system
  engine/          internal browser engine driver (spawns the engine binary,
                   JSON-RPC over stdio; error classification; state export)
  sources/         specialized source modules (web, search, github, youtube,
                   bilibili, reddit, rss, v2ex, twitter, xiaohongshu, linkedin,
                   boss, xueqiu, facebook, instagram, transcribe; xiaoyuzhou is planned and not implemented in this bundle)
  cli.py           `iralens` command
  mcp_server.py    IraLens MCP server (own unified tool surface)
```

### Unified data model (`model.Artifact`)

`title, url, source, kind, content, content_format, metadata (source-specific,
preserved), retrieval_method (generic: direct / browser / static-reader / api:…
/ cli:… — never names an implementation source), retrieved_at, discovered_from`
(provenance: the search query or page that surfaced this item).

### Unified errors (`errors.py`)

`PageUnavailableError, NavigationError, SourceUnavailableError,
AuthRequiredError, OperationUnsupportedError, OperationTimeoutError,
ExtractionError, SecurityBlockedError, EngineUnavailableError` — all carry a
user-safe `message` (scrubbed of credentials and internal ancestry) plus an
optional developer `detail`.

### Unified session (`session.py`)

One session across all capabilities: navigation history, discovered URLs with
provenance, and the engine's exported storage state (cookies + web storage)
persisted to `~/.iralens/state/`, so browser logins survive across runs
and source operations see the same session bookkeeping.

### Unified interface

- **Python:** `IraLens` facade — `search()`, `open()`, `read()`, all
  browser operations, `fetch(source, op, …)`, `sources()`, `doctor()`,
  `session()`, `close()`.
- **CLI:** `iralens <command>` (same surface).
- **MCP:** `iralens mcp` — one server named `iralens`, unified tools
  (`search`, `open`, `read`, `navigate`, `click`, `extract`, `source_fetch`,
  `session_state`, …). No tool or parameter exposes an implementation source.

### Security posture

- Every URL passes the SSRF guard before any fetch (private/internal hosts,
  userinfo, non-HTTP schemes rejected); the engine enforces a second guard.
- Internet content is returned as *untrusted data* (flagged in artifacts and
  MCP output); IraLens never executes instructions found in content.
- Credentials live in owner-only config, are injected into child processes
  only, and are scrubbed from all error text.
- No auto-login: authenticated sources require user-provided credentials or
  the user's own browser sessions.

### Deliberate non-goals

IraLens contains heuristic search ranking and a deterministic research layer
(planning, evidence, claim synthesis, contradiction candidates, provenance,
and replay); these provide leads, not fact-checking verdicts. It does not
operate an independently crawled search index or vector database, a managed
background scheduler, or a hosted service with an uptime/support promise.
Live quality, deployment and legal review are separate acceptance work.

## IraLens layers (search, reliability, research on top of the access core)

```
IraLens facade (core.py) — search(), search_api(), research(), open(), …
 ├── search/            schema, reformulation, fallback, filters, dedup, ranking,
 │                      HTTP/official API, optional SearXNG/Exa/paid adapters
 ├── reliability.py     failure taxonomy, retries, persistent circuit state, gate
 ├── cache.py           TTL disk cache (search responses, local reads)
 ├── content_guard.py   injection / invisible-character advisory flags
 ├── api.py             optional single-node HTTP API and Prometheus metrics
 ├── settings.py        tunables (config.yaml or IRALENS_* env)
 └── research/          planner loop, evidence, contradictions, synthesis,
                        provenance graph, replay, optional LLM adapters
```

Data flow for `research()`: question → expander → `search_api` per query →
top sources read locally, with optional local-browser fallback → claims →
contradiction candidates → cited statements → provenance graph and trace. The
pipeline is deterministic unless an optional LLM adapter is enabled. Its
heuristic conclusions are not independently verified facts.

