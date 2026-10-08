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

```bash
pip install half-iralens        # core system (search, reading, RSS, GitHub, V2EX, browser…)
halfiralens install-engine      # one-time: the local headless browser engine
halfiralens doctor              # see exactly what is available right now
```

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

46 tools, all capability-shaped: `search`, `open`, `read`, `source_fetch`,
`navigate`, `click`, `fill`, `extract`, `screenshot`, `pdf`, `cookies_*`,
`storage_state`, `tab_*`, `session_state`, `doctor`, …

## Capabilities

| Group | What you get |
|---|---|
| **Web discovery** | Web search (semantic bridge if configured, otherwise the built-in browser drives a public search engine), ranked results with provenance |
| **Page reading** | Any URL: specialized platform reader → fast static markdown → full local browser rendering, with anti-bot fallback |
| **Browser** | navigate/back/forward/reload, snapshot, markdown, links, click, fill, type, keys, select, scroll, JS eval, waits, in-page find, forms, structured CSS extraction, tabs, cookies, storage state, network log, console log, screenshots, PDF |
| **Specialized sources** | GitHub, YouTube, Twitter/X, Reddit, Bilibili, XiaoHongShu, V2EX, Xueqiu, Boss直聘, LinkedIn, Facebook, Instagram, RSS, podcast transcription — each with honest health status and multi-backend fallbacks |
| **Session** | One session across everything: history, discovered URLs with provenance, and browser login state that survives restarts |
| **Data model** | Every retrieval is an `Artifact`: title, url, source, content, source-specific metadata, retrieval method, timestamp, discovered_from — content always flagged untrusted |
| **Errors** | One taxonomy: page_unavailable, navigation_failed, source_unavailable, authentication_required, operation_unsupported, timeout, extraction_failed, blocked_by_security_policy, browser_engine_unavailable, session_state_error |

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

## Scope boundary

Half IraLens stops at access. It does not rank sources, score evidence,
detect contradictions, plan research, or synthesize reports — that is the
future IraLens intelligence layer, which will consume exactly this facade.
