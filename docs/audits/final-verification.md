# Final verification — v0.2.1, 2026-10-16

The working-project gate for the 0.2.1 cleanup release. Every check below
was actually run; the command and the observed output are recorded, and each
item ends in PASS, BLOCKED (network-bound, with the reason), or NOT RUN
(environment-bound, with the reason). Nothing is claimed that was not
observed.

**Environment:** Debian, Python 3.11.2. Egress is limited to GitHub and the
package indexes — live search engines and arbitrary web pages are
unreachable from here, which decides which checks are PASS versus BLOCKED.
Evidence for the live paths comes from the open-network audit on
2026-10-14 (`paid-search-replacement-audit-2026-10-14.md`).

## 1. Clean install from the README

Command (exact README quick start):

```bash
git clone <repo> && cd IraLens && ./scripts/setup.sh
source .venv/bin/activate
iralens doctor
```

Observed on a fresh clone with a fresh virtualenv: install completes,
`iralens 0.2.1` prints, `doctor: ok`. **PASS**
Full test suite in that clean environment: **429 passed, 14 skipped,
0 failed**; `ruff check --select E9,F,B .` clean; `mypy iralens` clean (67
source files). **PASS**

## 2. Test suite and skipped tests

`pytest -q`: **429 passed, 14 skipped, 0 failed.**
All 14 skips are in `tests/test_integration_live.py` with the explicit
reasons `network unavailable` / `network or browser engine unavailable` —
they are live-network and browser-engine tests that skip by design when
there is no general egress or installed engine. **PASS**
Live tests: **BLOCKED** on this network (reason above; they ran green —
416 passed, 0 failed — in the open-network audit on 2026-10-14).

## 3. Feature walk

One real example per feature; commands run in the clean environment.

| # | Feature | Command / driver | Observed output | Verdict |
|---|---|---|---|---|
| 1 | CLI search | `iralens search "solar cell efficiency" --limit 5` | Typed JSON `web search failed on every backend` + per-engine hint, exit **4** (documented contract for an unreachable network; live result production covered by the 2026-10-14 audit) | PASS (honest-failure contract) |
| 2 | Structured search | `iralens --json search-api "solar cell efficiency" --cache bypass` | `no_results_reason: engines_failed`, per-engine outcomes, `degraded: true`, exit 4 | PASS (contract) |
| 3 | CLI read | `iralens read https://github.com/pritamduttachoudhury0-collab/IraLens --max-chars 1500` | Live page read to clean text with untrusted-content notice | PASS (live) |
| 4 | CLI open | `iralens open https://github.com/pritamduttachoudhury0-collab/IraLens` | Source-aware open: kind `repo`, `api:github` metadata + README | PASS (live) |
| 5 | CLI fetch | `iralens --json fetch github search_repos query="headless browser" limit=3` | 3 live repositories as JSON objects | PASS (live) |
| 6 | Python API | `search_api` / `read` / `doctor` via `IraLens()` | `engines_failed` structured; live GitHub read via `direct`; doctor dict | PASS |
| 7 | MCP server | stdio `initialize`, `tools/list`, `tools/call` ×3 (`doctor`, `read`, `search_api`) | v0.2.1 handshake (**49 tools**); three typed results; clean exit 0 | PASS |
| 8 | HTTP API | `iralens-api` with a configured token | `/health` 200; unauthenticated search 401; `/v1/search` 503 + `engines_failed` body; SSRF read attempt 400 `blocked_by_security_policy` | PASS |
| 9 | Paid-style swap | Serper-shaped client with only base URL + key changed | `POST /search` returns `searchParameters`, `organic`, and the `iralens` diagnostics object (503 + structured body while engines are unreachable) | PASS (contract shape) |
| 10 | `/v1/read` | `curl /v1/read {url: api.github.com/rate_limit}` | 200, web fallback after the source reported the URL shape unsupported (the fallback bug found during this gate is fixed and regression-tested) | PASS (live) |
| 11 | Research mode | `iralens --json research "..." --rounds 2` | `stop_reason: search_failed`, `sources: 0`, explicit limitations, exit 4 (contract); full pipeline verified live below | PASS (contract) |
| 12 | Research over live data | `python scripts/live_github_research.py "headless browser"` | 12 claims, cited statements with confidence, provenance graph (32 nodes/31 edges), `replay_matches_live_run: true` | PASS (live) |
| 13 | PDF reading | `pdf_to_text` on a constructed text PDF (production call path) | 1 page extracted; `pdf_capabilities` reports state | PASS |
| 14 | Scanned documents | image-only PDF with `ocr_enabled=True` | Typed `extraction_failed: local PDF OCR needs pdftoppm and tesseract` with install hint; `pdf_capabilities` names the missing tools. OCR execution itself: **NOT RUN** — OCR tools are not installed on the verification machine | PASS (honest degradation) |
| 15 | Browser engine + backup | `iralens install-engine --status`, `iralens install-engine` | Status: `obscura (primary, not installed)`, backup `iralens-engine`, stealth off, control `127.0.0.1`. Install: typed `browser_engine_unavailable`, manual asset URL printed, exit 2 — the release CDN is unreachable from here. Engine execution: **NOT RUN** (binary unobtainable on the verification machine) | PASS (honest degradation) |
| 16 | Health memory | inspect `~/.iralens/state/search-health.sqlite3` after failed searches | `engine_circuit` rows per engine with failure counters, cooldowns, `last_failure_kind`; later runs persisted/re-read the state (`circuit_open` skips across processes) | PASS |
| 17 | Caching | two identical page reads on a fresh URL | first read 0.31 s (network), second 0.00 s (cache), identical content; cache entries in `~/.iralens/state/cache/` | PASS |
| 18 | Daily watch | `python scripts/daily_watch.py --output /tmp/daily-watch.json` | `overall: pass` — two live GitHub reads (repo HTML 0.757 s, repo API 0.364 s), JSON artifact written | PASS (live) |
| 19 | Interface verifier | `python scripts/verify_interfaces.py` | **20 PASS / 0 FAIL / 2 BLOCKED** of 22 (blocked: live engine answering, research finding web sources — network-bound) | PASS |
| 20 | Benchmark harness | `benchmarks/run_benchmark.py --self-test` | `SELF-TEST PASSED` (validates harness math; not a measurement) | PASS |
| 21 | Ranking benchmark | `scripts/ranking_benchmark.py` | MRR 0.8750 → 0.9750 (+0.1000), nDCG@5 0.9077 → 0.9815 (+0.0738) — matches the documented values | PASS |
| 22 | Honest degradation | `iralens navigate https://example.com` (no engine); `iralens read https://arxiv.org/pdf/1706.03762` (unreachable host); `iralens configure stealth=true` (isolated `IRALENS_HOME`) | Typed `browser_engine_unavailable` exit 2 with hint; typed `page_unavailable` exit 2; config written and echoed | PASS |
| 23 | Session state | `iralens session`, `iralens --json sources` | Persistent navigation history across processes; 16 sources listed | PASS |

Not runnable on the verification machine (stated in the docs, which say to
run them on the target host/network): the Docker/Compose stack (no Docker
daemon), the frozen 200-query/100-URL live benchmark and its load phase (no
egress; self-test above only), the Serper vertical tabs (intentionally not
synthesized).

## 4. Docs and repository hygiene

- Every relative link and heading anchor in every Markdown file checked
  programmatically: **all resolve** (external links verified on GitHub only).
- Every command shown in the docs was run once where the network allows;
  outcomes above. Docker commands are documented as target-host steps.
- Leftover scan: no `TODO`/`FIXME`, no stub functions, no unused files;
  remaining matches for "not implemented" are documented limitations
  themselves. Runtime dependencies are all exercised (`requests`,
  `feedparser`, `pyyaml`, `yt-dlp`, `pypdf`).
- Version consistent at `0.2.1`: `pyproject.toml`, `iralens/__init__.py`,
  MCP `clientInfo`, `--version`, badges.
- The one bug found by this gate — `/v1/read` failing on non-repo
  github.com URLs — was fixed (`OperationUnsupportedError` fallback) and
  pinned by a regression test.

## 5. What remains honestly unverified

Live search-engine production (last measured 2026-10-14: DuckDuckGo 15/15,
Bing 15/15), the frozen acceptance benchmark's full live run, OCR execution
on real scans, browser-engine execution, Docker builds, Windows setup,
paid-provider parity, and a seven-day daily-watch record. Each is listed in
`KNOWN_LIMITATIONS.md` with its reason; none is claimed here.
