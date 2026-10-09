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

Plain `pip install -e .` also works. `./scripts/setup.sh --no-dev` skips the test tools.

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
still exits 0; read the JSON, do not assume success from the exit code.

No platform-specific plugin or marketplace integration is provided. Any agent that can
run a shell command or start an MCP stdio server can use IraLens; the two blocks above
are the entire integration.

**Verify an install in one command:** `.venv/bin/python scripts/verify_interfaces.py`
(runs the CLI, Python, and MCP checks; needs `api.github.com` for the live checks).

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

    # Open anything — specialized platforms are handled natively,
    # everything else gets a real browser. One call, one system.
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
```

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
| **Web discovery** | Web search (semantic bridge if configured, otherwise the built-in browser drives a public search engine), ranked results with provenance |
| **Page reading** | Any URL: specialized platform reader → fast static markdown → full local browser rendering, with anti-bot fallback |
| **Browser** | navigate/back/forward/reload, snapshot, markdown, links, click, fill, type, keys, select, scroll, JS eval, waits, in-page find, forms, structured CSS extraction, tabs, cookies, storage state, network log, console log, screenshots, PDF |
| **Specialized sources** | GitHub, YouTube, Twitter/X, Reddit, Bilibili, XiaoHongShu, V2EX, Xueqiu, Boss直聘, LinkedIn, Facebook, Instagram, RSS, podcast transcription — each with honest health status and multi-backend fallbacks |
| **Session** | One session across everything: history, discovered URLs with provenance, and browser login state that survives restarts |
| **Data model** | Every retrieval is an `Artifact`: title, url, source, content, source-specific metadata, retrieval method, timestamp, discovered_from — content always flagged untrusted |
| **Errors** | One taxonomy: page_unavailable, navigation_failed, source_unavailable, authentication_required, operation_unsupported, timeout, extraction_failed, blocked_by_security_policy, browser_engine_unavailable, session_state_error, invalid_input (MCP: missing or malformed arguments) |

## How search works without a paid search API key

You do not need to sign up for a search API. The default chain drives public
search engines through free endpoints (DuckDuckGo HTML/lite, then Bing as a
fallback); an optional semantic bridge (Exa via `mcporter`) is used only if
you install it and only when the free engines fail. Results from all engines
are deduplicated, ranked with an explainable score breakdown, and returned
with per-engine outcomes. If every engine is blocked or unreachable, the
response says so (`no_results_reason`, `summary`) instead of failing silently.

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

- The static reader is a remote service (`r.jina.ai`): every URL you read in
  `static` mode is sent to it. Use `mode=browser` for pages you do not want
  shared with a third party.
- Search queries go to the public search engines in the chain; the optional
  semantic bridge sends them to its configured backend.
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

- `doctor` reports `web-search` as `off` only when neither the browser engine
  nor the semantic bridge is available; install the engine or check
  `install-engine --status`.
- Searches that return nothing carry a reason: `no_results` (genuinely
  empty), `filtered_out` (filters removed everything), or `engines_failed`
  (blocked/unreachable engines — check `outcomes` for `captcha`,
  `rate_limited`, `layout_changed`, …).
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

Not in this release: an HTTP API, a Docker image, and a published PyPI package
(see DECISIONS D-053 to D-054).
