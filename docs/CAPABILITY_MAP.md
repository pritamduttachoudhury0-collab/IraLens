# Capability Map — inventory of both implementation sources → IraLens

Required regression accounting: every capability found in the two sources is
listed with where it lives now and its verification boundary. ✅ = an offline
test/contract check passed in this workspace; ⚠ = implemented but a live
browser, network, or provider check is still required; ◻ = requires user
credentials/session that this environment cannot provide. An offline check is
not a claim of live availability.

## From the browser-engine source (all 37 engine tools + CLI modes)

| Engine capability | IraLens surface | Status |
|---|---|---|
| browser_navigate | `navigate` / `open(mode=browser)` | ⚠ wired; live engine/browser suite skipped here |
| browser_snapshot | `snapshot` | ⚠ live engine/browser suite skipped here |
| browser_markdown | `page_markdown` / `open` content | ⚠ live engine/browser suite skipped here |
| browser_links | `links` | ⚠ live engine/browser suite skipped here |
| browser_back / forward / reload | `back` / `forward` / `reload` | ⚠ live engine/browser suite skipped here |
| browser_click | `click` (selector or ref=) | ⚠ facade/MCP wired; live engine not verified |
| browser_fill / type / press_key / select_option | `fill` / `type_text` / `press_key` / `select_option` | ⚠ facade/MCP wired; live engine not verified |
| browser_scroll | `scroll` | ⚠ facade/MCP wired; live engine not verified |
| browser_evaluate | `evaluate_js` | ⚠ used internally by browser search; live engine not verified |
| browser_wait_for / wait_for_text | `wait_for` / `wait_for_text` | ⚠ live engine not verified |
| browser_search (in-page find) | `find_in_page` | ⚠ live engine not verified |
| browser_detect_forms / fill_form | `forms_detect` / `forms_fill` (map → fields array) | ✅ parameter shape checked against engine schema; live behavior unverified |
| browser_extract | `extract` | ⚠ live engine not verified |
| browser_count / get_attribute | `count` / `attribute` | ⚠ live engine not verified |
| browser_interactive_elements | `interactive_elements` | ⚠ live engine not verified |
| browser_tab_new / list / switch / close | `tab_new/list/switch/close` | ⚠ wired; live state transitions not verified |
| browser_get_cookies / set_cookie / clear_cookies | `cookies_get/set/clear` | ⚠ live browser cookie handling not verified |
| browser_storage_state / set_storage_state | `storage_state` / `set_storage_state` + automatic persist/restore in `Session` | ⚠ persistence path is implemented; live engine restart test skipped |
| browser_network_requests / console_messages | `network_log` / `console_log` | ⚠ live engine not verified |
| browser_screenshot / pdf | `screenshot(path)` / `pdf(path)` | ⚠ methods are wired; no real engine render verified |
| browser_close | `close` (persists state first) | ⚠ shutdown/persistence path implemented; live engine not verified |
| fetch (one-shot CLI) | `read` / `open` | ✅ offline reader/parser tests; live URL corpus unverified |
| scrape (bulk CLI) | `scrape(urls)` — per-URL failure isolation | ⚠ real URL smoke test skipped here |
| serve (CDP endpoint) | Not AI-facing; the engine binary retains it for advanced CDP clients | intentional |
| stealth / proxy / user-agent flags | config keys `stealth`, `proxy`, `user_agent` | ✅ launch configuration is covered by offline tests; live engine behavior not verified |
| SSRF guard, robots obedience, file:// block | facade SSRF guard plus engine-side controls | ✅ facade security suite; engine-side behavior not live-verified |

## From the capability-layer source (all 16 channels + mechanisms)

| Channel / mechanism | IraLens surface | Status |
|---|---|---|
| web (direct fetch + local extraction; opt-in remote reader; anti-bot detection + 5 MB cap) | `web` source, direct/static-reader backends of `read/open` | ✅ offline reader/parser tests; live URL corpus unverified |
| exa_search (mcporter + Exa) | `web-search` source, semantic-search backend | ◻ bridge absent here; provider behavior unverified |
| browser-based search engines (spec §18) | `web-search` browser-search backend (DDG HTML/lite) | ⚠ live network/browser test skipped here |
| github (gh CLI) | `github` source, gh-cli backend (repos/code/issues/…) | ◻ gh absent here |
| github (public API path) | `github` source, github-api backend (search/repo/readme/issues/releases/api) | ✅ live API/source checks plus one GitHub-only research run; does not validate general web search |
| youtube (yt-dlp metadata/subs/search/comments) | `youtube` source ops video/subtitles/search/comments | ⚠ live metadata/subs/comments not verified here |
| bilibili (bili-cli / bridge; yt-dlp deliberately excluded) | `bilibili` source, same backend order | ◻ CLI absent here; health honest |
| reddit (bridge / rdt-cli; honest "no anon path") | `reddit` source + browser fallback with auth-wall detection | ✅ fallback path tested via classifier; backends ◻ |
| twitter (twitter-cli/xreach + child-env creds; bridge) | `twitter` source, both backends, creds never exported to shell | ◻ no creds here |
| xiaohongshu (bridge / xhs-mcp / xsec_token rule) | `xiaohongshu` source; token rule documented in module | ◻ |
| linkedin (mcporter MCP + direct/reader fallback) | `linkedin` source + local-first fallback | ◻ (fallback ✅ code path shared with web) |
| boss (strict-CDP runbook: AUTH_EXPIRED, ENVIRONMENT_RISK, security-check ≠ login) | `boss` source with the same operational rules | ◻ |
| xueqiu (bridge; 400 = session problem) | `xueqiu` source with the same rule | ◻ |
| facebook / instagram (bridge passthrough) | `facebook` / `instagram` sources | ◻ |
| xiaoyuzhou + transcribe (Groq/OpenAI Whisper, provider-fallback discipline) | `transcribe` source (URL/local file, ffmpeg shrink, single-provider policy) | ◻ no key here |
| v2ex (hardened public API) | `v2ex` source hot/latest/topic/replies | ⚠ live source call skipped here |
| rss (feedparser) | `rss` source | ⚠ live feed call skipped here |
| doctor (per-channel health) | `doctor` — engine + source health in one report | ✅ offline contract tests; live probes not run |
| probe (missing/broken/timeout, stale-venv detection) | `proc.probe_command` | ✅ used by all health checks |
| ordered multi-backend routing + `<name>_backend` override | `Source.ordered_backends` + config override | ✅ |
| config (atomic owner-only YAML, symlink rejection) | `config.py` | ✅ |
| SSRF URL normalization | `security.normalize_public_http_url` | ✅ security suite (15 hostile URLs) |
| credential scrubbing (userinfo + query secrets) | `security.scrub_url_credentials` (+ free-text secrets) | ✅ security suite |
| cookie import helpers | config keys for per-platform tokens; browser cookies via engine `cookies_set`/`storage_state` | ✅ mechanism present |
| MCP server | IraLens stdio MCP server (49 tools); search/research artifacts remain marked untrusted | ✅ local stdio initialize/tools-list/source-call checks passed; no remote MCP service tested |
| installer/setup wizard, skill registration, update checker | intentionally not carried: scaffolding identity conflicts with the unified system; `install-engine` + `doctor` hints cover setup | intentional |

## Deliberate non-goals and verification boundary

IraLens does include heuristic search ranking and deterministic research
planning, claim/evidence synthesis, provenance, and contradiction candidates;
these are not verdicts or fact-checking guarantees. It does **not** operate an
independently crawled search index, vector database, managed background
scheduler, or hosted service with an uptime/support promise. Search ranking,
research quality, live provider coverage, and all skipped real-browser paths
require their own measurement; this capability inventory is not a quality or
acceptance report.
