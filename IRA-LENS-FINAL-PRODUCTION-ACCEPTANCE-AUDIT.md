# IRA-LENS FINAL PRODUCTION ACCEPTANCE AUDIT

Date: 2026-10-09 · Branch: `arena/f0e50e4a-iralens` · Base: `main` @
`0f90682e8bd8cc2b542bc58f92751aff4132f533` (verified against
`origin/main` via `git fetch`; local tree clean, no divergence before work
began).

## 0. Executive verdict (read this first)

**IraLens is materially more reliable, safer, and better verified than the
release on `main`, but it is NOT fully production-verified.** The sandbox
allows outbound traffic only to `github.com`, `api.github.com`,
`codeload.github.com`, `registry.npmjs.org`, `pypi.org`, and
`files.pythonhosted.org`. Therefore:

- Everything testable offline was tested and passes (267 tests, lint, types,
  dependency audit, clean-environment install, MCP protocol, adversarial CLI
  matrix).
- Live web search against real engines, the remote page reader, the engine
  asset CDN, and the live integration tests **could not be exercised here**.
  They are listed in §6 as unverified. Do not treat this document as proof
  that live search works; it is proof of everything that *could* be proven in
  this environment, and an exact list of what could not.

The core promise — useful web search and page reading **without a separately
configured paid search API key** — is implemented and preserved end-to-end
(D-067: free engines first; the optional Exa bridge is additive only), but
its live behavior remains unverified for the network reason above.

## 1. Repository state verification (Phase 1)

| Check | Result |
|---|---|
| Remote `origin/main` HEAD | `0f90682e8bd8cc2b542bc58f92751aff4132f533` ("Merge pull request #1 …"), fetched and confirmed |
| Working tree before work | clean; local branch already at `origin/main` |
| Commits `c5c258a`, `ecbf1aa` | **do not exist** in the repo or on GitHub (`git cat-file -t` → "Not a valid object name"). They were local-only reports and were neither merged nor recreated |
| Git history | single squash-merged release commit; no unrelated histories to merge |
| Baseline suite | 181 passed, 15 skipped (live tests skip without HTTPS egress) — matches AGENTS.md's recorded release-gate count |

Nothing that worked was rewritten. All changes are additive hardening on the
verified `main` state.

## 2. Changes made (Phase 2) — with decision references

| Area | Change | Ref |
|---|---|---|
| Query parsing | New `search/querytext.py`: symbol-aware tokens (`c++`, `c#`, `.net`, `f#`, `asp.net`), quoted-phrase extraction, CJK character bigrams, control/invisible-char stripping, 400-char length cap | D-068 |
| Ranking | Symbol-aware term overlap; quoted-phrase coverage factor; injection-flagged groups demoted (×0.5 relevance, never dropped) | D-073 |
| Dedup | Distinctly versioned pages (`/3.10/` vs `/3.12/`, `/v1` vs `/v2`) are never merged as near-duplicates | tests |
| Concurrency | Reformulated query variants run in a bounded thread pool; shared browser navigations serialized with a process-wide lock; deterministic merge order | D-069 |
| No-results honesty | `no_results_reason` gains `filtered_out`; `engines_failed` only when no engine answered; new `summary` sentence on every response | D-073 |
| `max_chars` | Enforced on **both** read backends (was ignored on the static path); CLI `read --max-chars` and MCP `read.max_chars` added; artifacts report `truncated` | D-071 |
| HTTP failures | Reader classifies 404 / 403 bot-check / 429 / 5xx / redirect-loop; soft-404 heuristic (short body + not-found markers); empty-response detection; existing anti-bot detection kept | D-071 |
| SSRF | `security.safe_urlopen`: DNS resolved up front, private/mixed answers refused, connection **pinned to the validated address** (no rebinding window), every redirect hop re-validated, https→http downgrade refused, ≤5 hops | D-072 |
| Resource limits | 5 MB response cap + overall 60 s read deadline (slow-drip protection) + per-socket timeout | D-071 |
| Engine install | Retry-with-backoff download; optional SHA-256 verification (`--checksum` / `HIL_ENGINE_SHA256`); startup probe **before** replacement (atomic swap — a bad download can never destroy a working engine); `install-engine --status`; honest `checksum_verified` reporting; tar `filter=` fallback for Python <3.12 (a real bug at `requires-python = 3.10`) | D-070 |
| Default chain | `duckduckgo → bing → semantic-search` (free engines first; no paid key needed) | D-067 |
| CLI errors | `ValueError`/`FilterError` now map to `invalid_input` (was `internal_error`) in the CLI, matching MCP | tests |
| Verification honesty | `verify_interfaces.py` reports PASS/FAIL/**BLOCKED** and lists unverified live checks; exit code ignores network-blocked checks | D-074 |

No security check was weakened and no assertion removed to make tests pass.
The two pre-existing test fakes whose signatures needed the new `max_chars`
keyword were updated; every original assertion was kept.

## 3. Verification evidence (Phase 3)

### 3.1 Test suite (offline)

| Run | Result |
|---|---|
| Baseline at `0f90682` | 181 passed, 15 skipped |
| Final, repo venv (Python 3.11.2) | **267 passed, 15 skipped, 0 failed** |
| Final, clean-environment venv (fresh checkout copy + `pip install .[dev]`) | **266 passed, 15 skipped** (the 267th test was added after the copy snapshot) |

New coverage: query parsing/sanitizing (13 tests), SSRF/redirect/DNS-pinning
(12 tests incl. a local-server redirect chain with simulated DNS), reader
hardening (19 tests), engine install safety (11 tests), concurrency &
serialization (4 tests), ranking/dedup/phrase/versioning (13 tests), CLI
regressions (3 tests), benchmark guard rails (2 tests).

### 3.2 Static checks and audits

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check --select E9,F,B halfiralens tests scripts` | All checks passed |
| Types | `mypy halfiralens --ignore-missing-imports` | Success: no issues in 60 files |
| Dependency vulnerabilities | `pip-audit` over the runtime set (requests, feedparser, pyyaml, yt-dlp) | No known vulnerabilities found |
| Engine release checksums | `gh api repos/h4ckf0r0day/obscura/releases/tags/v0.2.4` | Release exists; **no checksum asset is published** — therefore no checksum is claimed verified; optional operator-supplied checksum supported |

### 3.3 Adversarial matrix (through the real CLI)

| Attack / failure | Observed result |
|---|---|
| `read http://169.254.169.254/latest/meta-data/` | `blocked_by_security_policy` |
| `read http://127.0.0.1/admin`, `http://[::1]/x`, `http://10.0.0.5/` | `blocked_by_security_policy` |
| `read file:///etc/passwd`, `ftp://example.com/f` | `blocked_by_security_policy` |
| `read https://user:pass@example.com/x` | `blocked_by_security_policy` |
| Redirect → private target (unit, local server) | `SecurityBlockedError` on the hop |
| Redirect loop / >5 hops | stopped (policy cap; urllib repeat cap classified as `redirect_loop`) |
| Slow-drip response (1 byte/read, fake clock) | `OperationTimeoutError: slow_response_deadline` |
| Oversized response | `ExtractionError: response_too_large` |
| Soft 404 (200 + "not found" body) | `PageUnavailableError: soft_404` |
| 599-char query | `invalid_input: query is too long (599 chars); the limit is 400` |
| Control/bidi characters in query | stripped (`"so\u202elar\u200b query\x00"` → `solar query`) |
| Duplicate results across query variants | merged once (canonical-URL dedup), provenance kept |
| Injection-flagged result | demoted below clean result, still returned, flag surfaced |

### 3.4 Ranking benchmark (replacement — clearly labeled)

The original 20-case audit benchmark was **never committed** (single-commit
history; no benchmark artifact anywhere in the repo or release). It cannot be
recovered. `scripts/ranking_benchmark.py` is a reconstructed 20-case benchmark
of synthetic SERPs with explicit relevance judgments. Its numbers are **not**
results from the original benchmark.

Measured on this branch, new scorer vs a verbatim copy of the shipped legacy
scorer, relevance factor isolated (authority/freshness/agreement neutral):

| Metric | Legacy | New | Delta |
|---|---|---|---|
| MRR | 0.8750 | 0.9750 | **+0.1000** |
| nDCG@5 | 0.9077 | 0.9815 | **+0.0738** |

No case regressed; 18/20 cases discriminate correctly. Case 5 fails under
**both** scorers (low-coverage query + rank-1 distractor) and is recorded as
a remaining weakness (KNOWN_LIMITATIONS #39) rather than papered over.

### 3.5 Interface verification (`scripts/verify_interfaces.py`)

In this sandbox: **18 PASS, 0 FAIL, 2 BLOCKED** — CLI wiring, JSON output,
doctor, install-engine status, MCP initialize/tools/list (49 tools)/error
mapping, GitHub live fetch (reachable), and structured search/research
responses. BLOCKED (unverified): search engines answering at least one query;
research finding web sources. The same script run from the clean-environment
install reproduced these results.

### 3.6 MCP / agent integration

- MCP stdio protocol exercised end-to-end offline (initialize →
  notifications/initialized → tools/list → 6× tools/call → clean stdin-close
  exit) by `verify_interfaces.py`.
- **No external coding agent exists in this sandbox**, so integration with a
  real agent (Claude Code, Codex, …) was not exercised. The documented
  integration is a standard stdio MCP config plus shell commands; both are
  exercised above.

### 3.7 Clean-environment install

Fresh copy of the tree + fresh venv + `pip install ".[dev]"`: entry point
works (`halfiralens --version`), full suite passes, `verify_interfaces.py`
passes. Browser-engine binary install could not be executed: the asset CDN
(`objects.githubusercontent.com`) is outside the sandbox allowlist
(download attempt blocked, recorded). The installer's logic (retries,
checksum accept/reject, probe-before-replace, atomic swap, status) is fully
unit-tested offline instead.

## 4. Documentation updated

README (no-API-key search explanation, privacy, security, troubleshooting),
KNOWN_LIMITATIONS (items 17/33/36 rewritten; #38–#42 added), DECISIONS
(D-067…D-074), docs/THREAT_MODEL.md (fetch-time guard layer, remaining gaps),
docs/RELIABILITY.md (no-results taxonomy, read limits, query hygiene).

## 5. Acceptance criteria status

| Criterion | Status | Evidence |
|---|---|---|
| Useful search + reading without a paid search API key | **Implemented & preserved; live behavior unverified here** | D-067; §3.5 blocked checks |
| Query parsing edge cases (quotes, C++/C#/.NET, Unicode, ambiguity) | **Verified offline** | §3.1, tests/test_query_text.py |
| Ranking quality measurably improved, no regression | **Verified (MRR +0.10, nDCG@5 +0.074)** | §3.4 |
| Concurrent variants + dedup + versioned pages preserved | **Verified offline** | tests/test_search_hardening.py, tests/test_search_ranking.py |
| Meaningful no-results / low-confidence responses | **Verified offline** | `filtered_out`, `summary`, tests |
| Consistent `max_chars` on every read path | **Verified offline** | §3.1, facade test |
| HTTP errors / 403/404 / bot checks / rate limits / soft 404s | **Verified offline** | tests/test_web_reader.py |
| SSRF / redirect / DNS-rebinding protection | **Verified offline (local server + simulated DNS)** | tests/test_ssrf_fetch.py |
| Size limits / deadlines / slow-drip | **Verified offline** | tests/test_web_reader.py |
| Safe engine install (verify, retry, rollback, status) | **Logic verified offline; real download blocked by sandbox** | §3.7, tests/test_engine_install.py |
| Query length + control-char sanitization | **Verified offline + CLI** | §3.3 |
| Honest verification distinguishing local vs live | **Implemented** | §3.5 |

## 6. What remains unverified (network-blocked in this sandbox)

1. **Live web search** (DuckDuckGo HTML/lite, Bing) — parsers are
   fixture-tested; live markup was not seen. Layout drift would surface as
   `layout_changed`, not silently wrong results.
2. **Remote reader (`r.jina.ai`)** — hardening tested with fakes; the live
   service was not reachable.
3. **Browser-engine binary** — release exists (verified via GitHub API) but
   the asset CDN is blocked; the binary was not downloaded or executed.
4. **Live integration tests** (`tests/test_integration_live.py`) — all 15
   skipped, as at the release gate.
5. **Real coding-agent MCP session** — no agent available in the sandbox.
6. **Windows and macOS paths** — no such machines available.

## 7. Remaining risks

- Live-only behaviors (engine blocking rates, reader availability, markup
  drift) can only be observed with real Internet access.
- Injection flagging remains heuristic (English phrasings); advisory, not a
  boundary.
- Authority scoring is a coarse suffix table (documented).
- The benchmark is synthetic; it measures the scorer, not the live web.
- Without an operator-supplied checksum, engine integrity rests on HTTPS +
  startup probe (upstream publishes no checksum); the installer says so.

## 8. Reproduce

```bash
./scripts/setup.sh
.venv/bin/python -m pytest -q                       # 267 passed, 15 skipped
.venv/bin/ruff check --select E9,F,B halfiralens tests scripts
.venv/bin/mypy halfiralens --ignore-missing-imports
.venv/bin/pip-audit -r requirements (runtime deps)  # no known vulnerabilities
.venv/bin/python scripts/ranking_benchmark.py       # MRR/nDCG table
.venv/bin/python scripts/verify_interfaces.py       # PASS/FAIL/BLOCKED verdicts
```
