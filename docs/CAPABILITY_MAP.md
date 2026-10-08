# Capability Map — inventory of both implementation sources → Half IraLens

Required regression accounting: every capability found in the two sources is
listed with where it lives now and how it was verified. ✅ = verified by
automated test or live run in this workspace; ◻ = available, requires user
credentials/desktop session that this environment cannot provide (health
status reports honestly).

## From the browser-engine source (all 37 engine tools + CLI modes)

| Engine capability | Half IraLens surface | Status |
|---|---|---|
| browser_navigate | `navigate` / `open(mode=browser)` | ✅ tests C, D, E |
| browser_snapshot | `snapshot` | ✅ tests C, F |
| browser_markdown | `page_markdown` / `open` content | ✅ |
| browser_links | `links` | ✅ test D (single-link list normalization fixed) |
| browser_back / forward / reload | `back` / `forward` / `reload` | ✅ test D |
| browser_click | `click` (selector or ref=) | ✅ facade+MCP wired |
| browser_fill / type / press_key / select_option | `fill` / `type_text` / `press_key` / `select_option` | ✅ wired |
| browser_scroll | `scroll` | ✅ wired |
| browser_evaluate | `evaluate_js` | ✅ used internally by search backend |
| browser_wait_for / wait_for_text | `wait_for` / `wait_for_text` | ✅ wired |
| browser_search (in-page find) | `find_in_page` | ✅ wired |
| browser_detect_forms / fill_form | `forms_detect` / `forms_fill` (map → fields array) | ✅ param shape verified against engine schema |
| browser_extract | `extract` | ✅ wired |
| browser_count / get_attribute | `count` / `attribute` | ✅ wired |
| browser_interactive_elements | `interactive_elements` | ✅ wired |
| browser_tab_new / list / switch / close | `tab_new/list/switch/close` | ✅ test F (unknown tab → session_state_error, system continues) |
| browser_get_cookies / set_cookie / clear_cookies | `cookies_get/set/clear` | ✅ test E |
| browser_storage_state / set_storage_state | `storage_state` / `set_storage_state` + automatic persist/restore in `Session` | ✅ test E (cookie survives engine restart) |
| browser_network_requests / console_messages | `network_log` / `console_log` | ✅ wired |
| browser_screenshot / pdf | `screenshot(path)` / `pdf(path)` | ✅ render build installed |
| browser_close | `close` (persists state first) | ✅ test E |
| fetch (one-shot CLI) | `read` / `open` | ✅ |
| scrape (bulk CLI) | `scrape(urls)` — per-URL failure isolation | ✅ test B |
| serve (CDP endpoint) | Not AI-facing; the engine binary retains it for advanced CDP clients | intentional |
| stealth / proxy / user-agent flags | config keys `stealth`, `proxy`, `user_agent` | ✅ wired at engine start |
| SSRF guard, robots obedience, file:// block | kept (engine-side) + facade SSRF guard | ✅ test G/security suite |

## From the capability-layer source (all 16 channels + mechanisms)

| Channel / mechanism | Half IraLens surface | Status |
|---|---|---|
| web (Jina reader + anti-bot detection + 5 MB cap) | `web` source, static-reader backend of `read/open` | ✅ tests A, F |
| exa_search (mcporter + Exa) | `web-search` source, semantic-search backend | ◻ bridge absent here; browser-search backend ✅ test A |
| browser-based search engines (spec §18) | `web-search` browser-search backend (DDG HTML/lite) | ✅ test A live |
| github (gh CLI) | `github` source, gh-cli backend (repos/code/issues/…) | ◻ gh absent here |
| github (public API path) | `github` source, github-api backend (search/repo/readme/issues/releases/api) | ✅ tests B, C live |
| youtube (yt-dlp metadata/subs/search/comments) | `youtube` source ops video/subtitles/search/comments | ✅ live (metadata); subs/comments wired |
| bilibili (bili-cli / bridge; yt-dlp deliberately excluded) | `bilibili` source, same backend order | ◻ CLI absent here; health honest |
| reddit (bridge / rdt-cli; honest "no anon path") | `reddit` source + browser fallback with auth-wall detection | ✅ fallback path tested via classifier; backends ◻ |
| twitter (twitter-cli/xreach + child-env creds; bridge) | `twitter` source, both backends, creds never exported to shell | ◻ no creds here |
| xiaohongshu (bridge / xhs-mcp / xsec_token rule) | `xiaohongshu` source; token rule documented in module | ◻ |
| linkedin (mcporter MCP + Jina fallback) | `linkedin` source + static-reader fallback | ◻ (fallback ✅ code path shared with web) |
| boss (strict-CDP runbook: AUTH_EXPIRED, ENVIRONMENT_RISK, security-check ≠ login) | `boss` source with the same operational rules | ◻ |
| xueqiu (bridge; 400 = session problem) | `xueqiu` source with the same rule | ◻ |
| facebook / instagram (bridge passthrough) | `facebook` / `instagram` sources | ◻ |
| xiaoyuzhou + transcribe (Groq/OpenAI Whisper, provider-fallback discipline) | `transcribe` source (URL/local file, ffmpeg shrink, single-provider policy) | ◻ no key here |
| v2ex (hardened public API) | `v2ex` source hot/latest/topic/replies | ✅ test B live |
| rss (feedparser) | `rss` source | ✅ test B live |
| doctor (per-channel health) | `doctor` — engine + all 16 sources in one report | ✅ live |
| probe (missing/broken/timeout, stale-venv detection) | `proc.probe_command` | ✅ used by all health checks |
| ordered multi-backend routing + `<name>_backend` override | `Source.ordered_backends` + config override | ✅ |
| config (atomic owner-only YAML, symlink rejection) | `config.py` | ✅ |
| SSRF URL normalization | `security.normalize_public_http_url` | ✅ security suite (15 hostile URLs) |
| credential scrubbing (userinfo + query secrets) | `security.scrub_url_credentials` (+ free-text secrets) | ✅ security suite |
| cookie import helpers | config keys for per-platform tokens; browser cookies via engine `cookies_set`/`storage_state` | ✅ mechanism present |
| MCP server (`get_status` only) | replaced by the full Half IraLens MCP server (49 tools at Full IraLens Phase 3) | ✅ live handshake + calls |
| installer/setup wizard, skill registration, update checker | intentionally not carried: scaffolding identity conflicts with the unified system; `install-engine` + `doctor` hints cover setup | intentional |

## Deliberate non-goals (spec §17)

No ranking, credibility scoring, contradiction detection, research planning,
synthesis, custom index, vector DB, or background services exist anywhere in
this codebase.
