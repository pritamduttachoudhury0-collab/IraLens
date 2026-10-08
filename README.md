# Half IraLens

**One unified Internet-access system for AI agents.** Search the web, read any
page, drive a full browser, and reach specialized platforms — through a single
interface, a single data model, a single session, and a single error
taxonomy.

Half IraLens is the Internet-access foundation of the IraLens project. It
deliberately contains **no research intelligence**: no source ranking, no
credibility scoring, no research planning. Those belong to a future layer
that will sit on top of this one.

```
AI
 ↓
HALF IRALENS       ← you are here: CLI · Python API · MCP server
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
halfiralens install-engine      # optional: the local headless browser engine
halfiralens doctor              # see exactly what is available right now
```

Plain `pip install -e .` also works. `./scripts/setup.sh --no-dev` skips the test tools.

## For AI agents (Claude Code, Codex, OpenClaw, any terminal agent)

Two ways to call IraLens. Both need the setup above. Use the absolute path to
`.venv/bin/halfiralens` if the agent does not activate the venv.

**1. Shell (works with any agent that can run commands).** Every command accepts
`--json` (before or after the subcommand). Output is JSON on stdout. Untrusted-content
notices go to stderr.

```bash
cd /path/to/IraLens
.venv/bin/halfiralens --version
.venv/bin/halfiralens --json doctor                                  # what works right now
.venv/bin/halfiralens --json research "your question" --rounds 2     # cited report, stop_reason, trace
.venv/bin/halfiralens --json search-api "your query" --cache bypass  # ranked results + per-engine outcomes
.venv/bin/halfiralens --json fetch github search_repos query="headless browser" limit=5   # live GitHub data
.venv/bin/halfiralens --json read https://example.com                # one page, static read
```

**2. MCP (any MCP client that supports stdio servers).** Generic configuration:

```json
{ "mcpServers": { "half-iralens": { "command": "/ABS/PATH/IraLens/.venv/bin/halfiralens", "args": ["mcp"] } } }
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
`halfiralens doctor` always tells the truth about what works.

## The interface

### Python

```python
from halfiralens import HalfIraLens

with HalfIraLens() as hil:
    # Web discovery
    results = hil.search("rust headless browser", limit=5)

    # Open anything — specialized platforms are handled natively,
    # everything else gets a real browser. One call, one system.
    page = hil.open(results[0].url)

    # Full browser interaction
    hil.navigate("https://news.ycombinator.com")
    hil.click("a.storylink")
    hil.fill("input[name=q]", "query")
    data = hil.extract({"titles[]": "a.storylink", "url": "a.storylink@href"})
    hil.back()

    # Specialized sources
    repos  = hil.fetch("github",   "search_repos", query="cdp", limit=5)
    video  = hil.fetch("youtube",  "subtitles", url="https://youtu.be/…")
    feed   = hil.fetch("rss",      "read", url="https://hnrss.org/frontpage")
    topics = hil.fetch("v2ex",     "hot")

    # One session spans all of it
    print(hil.session_state())
```

### CLI

```bash
halfiralens search "query" --limit 5
halfiralens open https://github.com/owner/repo     # source-aware open
halfiralens read https://example.com               # fast static read
halfiralens fetch github search_repos query=cdp limit=5
halfiralens navigate URL && halfiralens links && halfiralens snapshot
halfiralens click "a.next" && halfiralens extract '{"rows[]": "tr"}'
halfiralens screenshot page.png
halfiralens sources | doctor | session
halfiralens mcp                                    # start the MCP server
```

### MCP

```json
{ "mcpServers": { "half-iralens": { "command": "halfiralens", "args": ["mcp"] } } }
```

49 tools (the `TOOLS` list in `halfiralens/mcp_server.py`), all capability-shaped: `search`, `open`, `read`, `source_fetch`,
`navigate`, `click`, `fill`, `extract`, `screenshot`, `pdf`, `cookies_*`,
`storage_state`, `tab_*`, `session_state`, `doctor`, …

## Full IraLens: search, reliability, research

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

## Security

- **SSRF guard** on every URL (private hosts, metadata endpoints, userinfo
  tricks, non-HTTP schemes all rejected) — plus a second guard inside the
  browser engine.
- **Internet content is data, never instructions.** Retrievals are flagged
  `untrusted` and MCP output carries an explicit notice; Half IraLens never
  obeys directives found in pages, search results, or documents.
- **Credentials** live in an owner-only config file, are injected into child
  processes only, and are scrubbed from every error message.
- **No auto-login.** Authenticated platforms use credentials *you* provide or
  *your own* browser sessions.

## Configuration

`~/.half-iralens/config.yaml` (or `HALF_IRALENS_HOME`), overridable with
`HIL_*` environment variables:

```bash
halfiralens configure groq_key=...            # transcription
halfiralens configure twitter_auth_token=...  # X access
halfiralens configure stealth=true            # engine anti-detection
halfiralens configure proxy=http://...        # engine proxy
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

Full IraLens = the access layer (search, reading, browsing, specialized sources)
plus the research layer built on it (planning, evidence evaluation, contradiction
candidates, synthesis, provenance). The research layer is deterministic by default
and uses no model. Its outputs are leads with stated limits, not verified answers.
See `docs/RESEARCH.md` and `KNOWN_LIMITATIONS.md`.

Not in this release: an HTTP API, a Docker image, and a published PyPI package
(see DECISIONS D-053 to D-054).
