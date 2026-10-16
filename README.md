# IraLens

**One unified Internet-access system for AI agents.** Search the web, read any
page, drive a full browser, and reach specialized platforms — through a single
interface, a single data model, a single session, and a single error
taxonomy.

IraLens is one system in two layers: the Internet-access layer (search,
reading, browsing, specialized sources) and a deterministic research layer
on top of it (`iralens/research/`) that plans queries, reads sources, and
produces cited reports — no model required.

```
AI
 ↓
IRALENS            ← you are here: CLI · Python API · MCP server
 ↓
The Internet       ← search engines, web pages, browsers, platforms
```

---

## Install

The package is **not yet published to PyPI**, so install from a checkout:

```bash
git clone https://github.com/pritamduttachoudhury0-collab/IraLens.git
cd IraLens
./scripts/setup.sh              # Linux/macOS: creates .venv, installs, runs a health check
# Windows (UNTESTED, see scripts/setup.ps1): .\scripts\setup.ps1
source .venv/bin/activate
iralens install-engine      # optional: the local headless browser engine
iralens doctor              # see exactly what is available right now
```

Plain `pip install -e .` also works from the checkout. `./scripts/setup.sh --no-dev` skips the test tools. The package is not published to PyPI yet.

**Run as a self-hosted search service (Docker Compose).** This starts IraLens'
HTTP API and a private SearXNG metasearch instance on one Docker network; only
the IraLens API port is published. It needs Docker Compose and an API token:

```bash
export IRALENS_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
docker compose up --build -d
curl -sS http://127.0.0.1:8000/health
curl -sS -H "X-API-KEY: $IRALENS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"q":"solar cell efficiency","num":5}' \
  http://127.0.0.1:8000/search
```

`POST /search` uses a Serper-shaped basic request/response. The native,
fully diagnostic contract is `POST /v1/search`; page reading is `POST /v1/read`;
Prometheus metrics are at `/metrics` (authenticated). See
[`docs/API.md`](docs/API.md). Use TLS via a reverse proxy before exposing this
service outside a trusted network. Docker build/runtime were not available for
verification in this workspace; see `docs/PHASE_STATUS.md`.

## For AI agents (Claude Code, Codex, OpenClaw, any terminal agent)

Two ways to call IraLens. Both need the setup above. Use the absolute path to
`.venv/bin/iralens` if the agent does not activate the venv.

**1. Shell (works with any agent that can run commands).** Every command accepts
`--json` (before or after the subcommand). Output is JSON on stdout. Untrusted-content
notices go to stderr.

```bash
cd /path/to/IraLens
.venv/bin/iralens --version
.venv/bin/iralens --json doctor                                  # what works right now
.venv/bin/iralens --json research "your question" --rounds 2     # cited report, stop_reason, trace
.venv/bin/iralens --json search-api "your query" --cache bypass  # ranked results + per-engine outcomes
.venv/bin/iralens --json fetch github search_repos query="headless browser" limit=5   # live GitHub data
.venv/bin/iralens --json read https://example.com                # one page, static read
```

**2. MCP (any MCP client that supports stdio servers).** Generic configuration:

```json
{ "mcpServers": { "iralens": { "command": "/ABS/PATH/IraLens/.venv/bin/iralens", "args": ["mcp"] } } }
```

The server exposes 49 tools, including `research`, `search_api`, `search`, `read`,
`source_fetch`, and `doctor`. Tool errors are JSON with an `error` type and a `message`.

**Read the output correctly.** `research` returns leads, not verified answers. Check
`stop_reason`, `limitations`, and each statement's `status` and `source_ids`. If the
web search engines are unreachable, `stop_reason` is `search_failed` and the command
exits 4 (see the exit-code contract below) — the JSON on stdout still explains why.

No platform-specific plugin or marketplace integration is provided. Any agent that can
run a shell command or start an MCP stdio server can use IraLens; the two blocks above
are the entire integration.

**Verify an install in one command:** `.venv/bin/python scripts/verify_interfaces.py`
(runs the CLI, Python, and MCP checks; needs `api.github.com` for the live checks).

**Exit-code contract** (stable; agents may branch on it):

| code | meaning |
|------|---------|
| 0 | success — including an honest zero-results answer |
| 2 | handled error with a typed JSON message on stderr (bad input, bad URL, forbidden target, unavailable source/page) |
| 3 | unexpected internal error |
| 4 | every search engine failed — the query is unanswered; see `outcomes` / `stop_reason` in the output |
| 130 | interrupted (Ctrl-C) |

Optional capabilities unlock as their (free) tools appear — `yt-dlp` for
YouTube, `gh` for private GitHub repos and code search, the desktop browser
bridge for login-walled platforms, `mcporter`+Exa for semantic search.
`iralens doctor` always tells the truth about what works.

## The interface

### Python

```python
from iralens import IraLens

with IraLens() as lens:
    # Web discovery
    results = lens.search("rust headless browser", limit=5)

    # Open anything — specialized sources first, then local static reading;
    # a local browser fallback handles JS-heavy pages when installed.
    page = lens.open(results[0].url)

    # Full browser interaction
    lens.navigate("https://news.ycombinator.com")
    lens.click("a.storylink")
    lens.fill("input[name=q]", "query")
    data = lens.extract({"titles[]": "a.storylink", "url": "a.storylink@href"})
    lens.back()

    # Specialized sources
    repos  = lens.fetch("github",   "search_repos", query="cdp", limit=5)
    video  = lens.fetch("youtube",  "subtitles", url="https://youtu.be/…")
    feed   = lens.fetch("rss",      "read", url="https://hnrss.org/frontpage")
    topics = lens.fetch("v2ex",     "hot")

    # One session spans all of it
    print(lens.session_state())
```

### CLI

```bash
iralens search "query" --limit 5
iralens open https://github.com/owner/repo     # source-aware open (--max-chars N)
iralens read https://example.com --max-chars 8000   # fast static read
iralens fetch github search_repos query=cdp limit=5
iralens navigate URL && iralens links && iralens snapshot
iralens click "a.next" && iralens extract '{"rows[]": "tr"}'
iralens screenshot page.png
iralens sources | doctor | session
iralens mcp                                    # start the MCP server
iralens serve                                  # local HTTP API on 127.0.0.1:8000
```

### HTTP API

```bash
export IRALENS_API_KEY="replace-with-a-long-random-token"
IRALENS_API_HOST=0.0.0.0 iralens-api --port 8000
curl -H "Authorization: Bearer $IRALENS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"solar cell efficiency","max_results":5}' \
  http://localhost:8000/v1/search
```

A non-loopback bind requires `IRALENS_API_KEY` with at least 32 characters.
The service exposes native
`POST /v1/search`, Serper-shaped `POST /search`, `POST /v1/read`, `GET /health`,
authenticated `GET /v1/health/search`, and authenticated Prometheus `/metrics`.
It is a single-node service; use a TLS reverse proxy, resource limits, and
persistent local state in production. See the [`API reference`](docs/API.md) and
[`operations guide`](docs/OPERATIONS.md).

### MCP

```json
{ "mcpServers": { "iralens": { "command": "iralens", "args": ["mcp"] } } }
```

49 tools (the `TOOLS` list in `iralens/mcp_server.py`), all capability-shaped: `search`, `open`, `read`, `source_fetch`,
`navigate`, `click`, `fill`, `extract`, `screenshot`, `pdf`, `cookies_*`,
`storage_state`, `tab_*`, `session_state`, `doctor`, …

## Search, reliability, research

- `search_api(query, filters, options)` returns ranked results with per-engine
  outcomes, fallbacks, filter report, dedup log, cache status, and security flags.
  `search()` is unchanged. See `docs/SEARCH.md`.
- `research(question, options)` runs a bounded multi-round loop. It reads top
  sources, flags possible contradictions, and returns cited statements with a
  provenance graph and a replayable trace. See `docs/RESEARCH.md`.
- Reliability, caching, and content-security policy: `docs/RELIABILITY.md`,
  `docs/THREAT_MODEL.md`. Known gaps: `KNOWN_LIMITATIONS.md`. Decisions: `DECISIONS.md`.

## Capabilities

| Group | What you get |
|---|---|
| **Web discovery** | Keyless search chain (DuckDuckGo/Bing public HTML endpoints, Wikipedia's official API, optional private SearXNG), ranked results with provenance; browser rendering is a fallback, not a search index |
| **Page reading** | Any URL: specialized platform reader → fast static markdown (HTML **and PDF**) → full local browser rendering, with anti-bot and JS-shell fallback |
| **Browser** | navigate/back/forward/reload, snapshot, markdown, links, click, fill, type, keys, select, scroll, JS eval, waits, in-page find, forms, structured CSS extraction, tabs, cookies, storage state, network log, console log, screenshots, PDF |
| **Specialized sources** | GitHub, YouTube, Twitter/X, Reddit, Bilibili, XiaoHongShu, V2EX, Xueqiu, Boss直聘, LinkedIn, Facebook, Instagram, RSS, podcast transcription — each with honest health status and multi-backend fallbacks |
| **Session** | One session across everything: history, discovered URLs with provenance, and browser login state that survives restarts |
| **Data model** | Every retrieval is an `Artifact`: title, url, source, content, source-specific metadata, retrieval method, timestamp, discovered_from — content always flagged untrusted |
| **Errors** | One taxonomy: page_unavailable, navigation_failed, source_unavailable, authentication_required, operation_unsupported, timeout, extraction_failed, blocked_by_security_policy, browser_engine_unavailable, session_state_error, invalid_input (MCP: missing or malformed arguments) |

## How search works without a paid search API key

You can run IraLens without a search-provider key. The default chain is
DuckDuckGo HTML/lite, Bing HTML (experimental), Wikipedia's official MediaWiki
API, and the optional `mcporter`/Exa bridge when installed. A SearXNG backend is
inserted only when you explicitly configure an instance; IraLens no longer
contacts bundled public SearXNG instances by default. A separately configured
Brave, Serper, or Tavily key can enable one **optional paid emergency fallback**
after free sources fail; it is disabled otherwise.

This is a keyless federated client, **not an independently crawled search
index**. Availability, source terms, ranking parity, and large-scale use are
not guaranteed. Search sources receive your queries; check
[`docs/SOURCE_POLICY.md`](docs/SOURCE_POLICY.md) before deployment. The system
returns per-engine outcomes and an honest `engines_failed` reason rather than
inventing results.

**Private SearXNG.** Run the included Compose stack, or launch the standalone
service in `deploy/searxng/`:

```bash
export SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
cd deploy/searxng && docker compose up -d
export IRALENS_SEARXNG_INSTANCES="http://127.0.0.1:8888"
export IRALENS_ALLOWED_PRIVATE_HOSTS="127.0.0.1:8888"   # search transport only
```

The private server still queries the upstream engines you enable in SearXNG;
self-hosting does not remove those upstream services' terms or rate limits.
Public instance URLs are intentionally not included as defaults.

**Reliability benchmark.** `benchmarks/` is a frozen 200-query / 100-URL
acceptance benchmark with the thresholds IraLens must hold (availability,
quality on dev + held-out splits, engine redundancy, latency, load, extraction,
honesty). One command, no key required:

```bash
.venv/bin/python benchmarks/run_benchmark.py --all --with-load --network home
```

It bypasses the cache, paces requests realistically, writes `results.json`,
appends a row to `BENCHMARKS.md` only when every threshold passes, and labels
anything it could not run as `not_run` with the reason. See
`benchmarks/README.md`.

## Security

- **SSRF guard** on every URL (private hosts, metadata endpoints, userinfo
  tricks, non-HTTP schemes all rejected) — plus a second guard inside the
  browser engine.
- **Redirect-validated, DNS-pinned fetches** on the page-reading path: every
  redirect hop is re-checked against the public-URL policy, https→http
  downgrades and long chains are refused, hostnames are resolved up front and
  the connection is pinned to the validated address (no DNS-rebinding window).
- **Hardened page reads**: response size cap (5 MB default), overall read
  deadline (slow-drip protection), HTTP status classification (404, 403
  bot-checks, 429, 5xx, redirect loops), soft-404 detection, and a consistent
  `max_chars` budget on every backend (`truncated` is reported in metadata).
- **Query hygiene**: control/invisible characters are stripped and queries are
  length-capped (400 chars) before any engine sees them.
- **Safe engine installation**: downloads retry on transient failures, the
  binary is startup-probed *before* it replaces anything (a failed install
  never destroys a working engine), and a SHA-256 is verified whenever one is
  supplied (`install-engine --checksum` or `IRALENS_ENGINE_SHA256`). Upstream
  publishes no checksum, so the install reports `checksum_verified: false`
  honestly rather than claiming verification.
- **Internet content is data, never instructions.** Retrievals are flagged
  `untrusted` and MCP output carries an explicit notice; IraLens never
  obeys directives found in pages, search results, or documents. Results
  flagged for prompt injection are demoted in ranking — never silently dropped.
- **Credentials** live in an owner-only config file, are injected into child
  processes only, and are scrubbed from every error message.
- **No auto-login.** Authenticated platforms use credentials *you* provide or
  *your own* browser sessions.

Security boundaries and known gaps: `docs/THREAT_MODEL.md` and
`KNOWN_LIMITATIONS.md`.

## Privacy

- Reads are local-first: pages are fetched directly and extracted locally;
  the browser-rendering fallback also runs locally. The remote reader
  (`r.jina.ai`) is **off by default** and only used when you opt in
  (`IRALENS_READ_REMOTE_READER_ENABLED=1`), and then only when the direct read
  fails. URLs sent to it are shared with that third party. Research uses the
  same local-first reader by default.
- Search queries go to the configured search engines; the optional semantic
  bridge or explicitly configured paid fallback also receives them. The
  optional reference benchmark replays its frozen queries to the chosen
  provider. Review `docs/SOURCE_POLICY.md` before enabling either.
- Nothing is sent to the project authors. Session state and caches stay in
  `~/.iralens` (or `IRALENS_HOME`).

## Troubleshooting and verification

```bash
iralens doctor                      # what works right now, per source
iralens install-engine --status     # browser engine: installed? executable?
iralens install-engine              # install it (retries, probe-before-replace)
.venv/bin/python scripts/verify_interfaces.py   # CLI + Python + MCP checks
.venv/bin/python -m pytest -q           # offline suite; live tests auto-skip
```

- `doctor` reports `web-search` health honestly: free engines fetch results
  pages over direct HTTP (no browser engine needed); the engine, when
  installed, is a rendered fallback for bot walls (check
  `install-engine --status`).
- Searches that return nothing carry a reason: `no_results` (genuinely
  empty), `filtered_out` (filters removed everything), or `engines_failed`
  (blocked/unreachable engines — check `outcomes` for `captcha`,
  `rate_limited`, `layout_changed`, …).
- Text PDFs use the embedded text layer (`pypdf`). Scanned PDFs use free,
  **local** OCR automatically *if* `pdftoppm` and `tesseract` are installed;
  `iralens doctor` lists missing tools. Without them, scans fail with a typed
  `pdf_ocr_support_missing`, never as binary noise or an unannounced empty page.
  Opt out with `IRALENS_PDF_OCR_ENABLED=false`; no cloud OCR is used.
- Reads that fail tell you why: `http_404`, `http_403` (bot check), `http_429`,
  `soft_404`, `antibot_challenge`, `response_too_large`, `slow_response_deadline`,
  `redirect_loop`, or `blocked_by_security_policy`.

## Configuration

`~/.iralens/config.yaml` (or `IRALENS_HOME`), overridable with
`IRALENS_*` environment variables:

```bash
iralens configure groq_key=...            # transcription
iralens configure twitter_auth_token=...  # X access
iralens configure stealth=true            # engine anti-detection
iralens configure proxy=http://...        # engine proxy
```

Search/read tuning is available as config keys or `IRALENS_*` environment
variables (the environment wins):

```bash
IRALENS_SEARXNG_INSTANCES="http://127.0.0.1:8888"  # no public instance is used by default
IRALENS_ALLOWED_PRIVATE_HOSTS="127.0.0.1:8888"      # self-hosted search backend opt-in (search only)
IRALENS_SEARXNG_COOLDOWN_SECONDS=600                 # durable per-instance cooldown
IRALENS_SEARXNG_TIMEOUT_SECONDS=20                  # per-instance request budget
IRALENS_BACKUP_SEARCH_PROVIDER=serper                # optional paid emergency fallback
IRALENS_BACKUP_SEARCH_API_KEY=...                    # configure only if desired
IRALENS_PDF_OCR_ENABLED=false                        # optional opt-out; local OCR auto-attempts if tools exist
IRALENS_READ_REMOTE_READER_ENABLED=1                  # opt in to r.jina.ai after direct read fails
IRALENS_API_KEY=... IRALENS_API_HOST=0.0.0.0            # authenticated HTTP API (TLS proxy recommended)
IRALENS_ENGINE_STARTUP_TIMEOUT_SECONDS=20              # engine handshake budget (a dead binary fails in ~1s)
IRALENS_BIN_DIR=~/.local/bin             # where install-engine puts the iralens-engine shim
IRALENS_ENGINE_PATH=... IRALENS_ENGINE_SHA256=... IRALENS_ENGINE_DIR=...  # engine overrides
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/          # unit tests run offline; live tests auto-skip
```

`ARCHITECTURE.md` documents the internal fusion design; `NOTICE` carries the
attributions and licenses of the incorporated open-source components.

## License

Apache License 2.0 (`LICENSE`). `NOTICE` carries the copyright line and the
attributions for incorporated open-source components, including the MIT-licensed
code adapted from Agent Reach. See `LICENSE_AUDIT.md` for the component audit.

## Scope boundary

IraLens = the access layer (search, reading, browsing, specialized sources)
plus the research layer built on it (planning, evidence evaluation, contradiction
candidates, synthesis, provenance). The research layer is deterministic by default
and uses no model. Its outputs are leads with stated limits, not verified answers.
See `docs/RESEARCH.md` and `KNOWN_LIMITATIONS.md`.

Not in this release: a published PyPI package, managed endpoint, service-level
uptime/support promise, independent web index, or demonstrated paid-provider
parity. HTTP API and Docker files are included but still require target-host
build/deployment verification. See `docs/PHASE_STATUS.md`.
