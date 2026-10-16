# Production acceptance audit — 2026-10-09

**Scope:** hardening verification for the search, reading, and engine-install
paths. **Environment class:** restricted egress — only package indexes and
GitHub were reachable, so everything testable offline was tested, and every
live-network item is listed as unverified rather than implied.

## Verdict

The system is materially more reliable, safer, and better verified after this
hardening pass — but not fully production-verified:

- Everything testable offline was tested and passed: 267 tests, lint, types,
  dependency audit, clean-environment install, MCP protocol, adversarial CLI
  matrix.
- Live web search against real engines, the remote reader, the engine asset
  CDN, and the live integration tests could not be exercised there. They are
  listed as unverified below. This document proves what could be proven in a
  restricted environment, and names exactly what could not.

The core promise — useful web search and page reading **without a separately
configured paid search API key** — is implemented end to end (free engines
first; the optional Exa bridge is additive only), with live behavior
unverified in that environment for the network reason above.

## Changes verified (hardening pass)

| Area | Change |
|---|---|
| Query parsing | `search/querytext.py`: symbol-aware tokens (`c++`, `c#`, `.net`, `f#`, `asp.net`), quoted-phrase extraction, CJK bigrams, control/invisible-char stripping, 400-char cap |
| Ranking | Symbol-aware term overlap; quoted-phrase coverage; injection-flagged groups demoted (×0.5 relevance, never dropped) |
| Dedup | Distinctly versioned pages (`/3.10/` vs `/3.12/`, `/v1` vs `/v2`) never merged as near-duplicates |
| Concurrency | Reformulated variants in a bounded pool; shared browser navigations serialized; deterministic merge order |
| No-results honesty | `no_results_reason` gains `filtered_out`; `engines_failed` only when no engine answered; `summary` sentence on every response |
| `max_chars` | Enforced on **both** read backends (was ignored on the static path); artifacts report `truncated` |
| HTTP failures | Reader classifies 404 / bot-check 403 / 429 / 5xx / redirect loops; soft-404 heuristic; empty-response detection |
| SSRF | `security.safe_urlopen`: DNS resolved up front, private/mixed answers refused, connection pinned to the validated address, every redirect hop re-validated, https→http downgrade refused, ≤5 hops |
| Resource limits | 5 MB response cap + 60 s overall read deadline (slow-drip protection) + per-socket timeout |
| Engine install | Retry-with-backoff download; optional SHA-256 verification; startup probe **before** replacement (atomic swap); `install-engine --status`; honest `checksum_verified` reporting; tar `filter=` fallback for Python <3.12 (a real bug at `requires-python = 3.10`) |
| Default chain | `duckduckgo → bing → semantic-search` (free engines first) |
| CLI errors | `ValueError`/`FilterError` map to `invalid_input` (was `internal_error`), matching MCP |
| Verification honesty | `verify_interfaces.py` reports PASS/FAIL/**BLOCKED**, lists unverified live checks, and its exit code ignores network-blocked checks |

No security check was weakened and no assertion removed to make tests pass.
The two pre-existing test fakes whose signatures needed the new `max_chars`
keyword were updated; every original assertion was kept.

## Verification evidence

### Test suite (offline)

| Run | Result |
|---|---|
| Baseline before hardening | 181 passed, 15 skipped |
| Final, repository venv (Python 3.11.2) | **267 passed, 15 skipped, 0 failed** |
| Final, clean-environment venv (fresh copy + `pip install .[dev]`) | **266 passed, 15 skipped** (the 267th test was added after the copy snapshot) |

New coverage: query parsing/sanitizing (13 tests), SSRF/redirect/DNS-pinning
(12 tests incl. a local-server redirect chain with simulated DNS), reader
hardening (19 tests), engine install safety (11 tests), concurrency and
serialization (4 tests), ranking/dedup/phrase/versioning (13 tests), CLI
regressions (3 tests), benchmark guard rails (2 tests).

### Static checks and audits

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check --select E9,F,B iralens tests scripts` | All checks passed |
| Types | `mypy iralens --ignore-missing-imports` | Success: no issues in 60 files |
| Dependency vulnerabilities | `pip-audit` over the runtime set | No known vulnerabilities found |
| Engine release checksums | GitHub API for the pinned engine release | Release exists; **no checksum asset is published** — therefore no checksum is claimed verified; operator-supplied checksum supported |

### Adversarial matrix (through the real CLI)

| Attack / failure | Observed result |
|---|---|
| `read http://169.254.169.254/latest/meta-data/` | `blocked_by_security_policy` |
| `read http://127.0.0.1/admin`, `http://[::1]/x`, `http://10.0.0.5/` | `blocked_by_security_policy` |
| `read file:///etc/passwd`, `ftp://example.com/f` | `blocked_by_security_policy` |
| `read https://user:pass@example.com/x` | `blocked_by_security_policy` |
| Redirect → private target (unit, local server) | `SecurityBlockedError` on the hop |
| Redirect loop / >5 hops | stopped (policy cap; repeated redirects classified `redirect_loop`) |
| Slow-drip response (1 byte/read, fake clock) | `OperationTimeoutError: slow_response_deadline` |
| Oversized response | `ExtractionError: response_too_large` |
| Soft 404 (200 + "not found" body) | `PageUnavailableError: soft_404` |
| 599-char query | `invalid_input: query is too long (599 chars); the limit is 400` |
| Control/bidi characters in query | stripped (`"so\u202elar\u200b query\x00"` → `solar query`) |
| Duplicate results across query variants | merged once (canonical-URL dedup), provenance kept |
| Injection-flagged result | demoted below clean result, still returned, flag surfaced |

### Ranking benchmark (labeled replacement)

The original 20-case audit benchmark was never committed to the repository
and does not exist anywhere in its history. `scripts/ranking_benchmark.py`
is a reconstructed 20-case benchmark of synthetic result pages with explicit
relevance judgments; its numbers are **not** results from the lost original.

Measured on the hardened code, the new scorer vs a verbatim copy of the
shipped legacy scorer, relevance factor isolated (authority/freshness/
agreement neutral):

| Metric | Legacy | New | Delta |
|---|---|---|---|
| MRR | 0.8750 | 0.9750 | **+0.1000** |
| nDCG@5 | 0.9077 | 0.9815 | **+0.0738** |

No case regressed; 18/20 cases discriminate correctly. One case fails under
**both** scorers (low-coverage query + rank-1 distractor) and is recorded as
a remaining weakness in `KNOWN_LIMITATIONS.md`, not papered over.

### Interface verification

`scripts/verify_interfaces.py`: **18 PASS, 0 FAIL, 2 BLOCKED** — CLI wiring,
JSON output, doctor, install-engine status, MCP initialize/tools/list (49
tools)/error mapping, live GitHub fetch (reachable there), and structured
search/research responses. BLOCKED (unverified): an engine answering at
least one query, and research finding web sources. A clean-environment
install reproduced these results.

### MCP / agent integration

- MCP stdio protocol exercised end to end offline (initialize →
  notifications/initialized → tools/list → 6× tools/call → clean exit).
- A full session with a real coding agent was not exercised; the documented
  integration is a standard stdio MCP config plus shell commands, both of
  which are exercised above.

### Clean-environment install

Fresh copy of the tree + fresh venv + `pip install ".[dev]"`: entry point
works (`iralens --version`), full suite passes, interface verification
passes. The engine binary install could not run there (the release-asset
CDN was unreachable); the installer's logic (retries, checksum
accept/reject, probe-before-replace, atomic swap, status) is fully
unit-tested offline instead.

## Acceptance criteria status

| Criterion | Status | Evidence |
|---|---|---|
| Useful search + reading without a paid search API key | **Implemented; live behavior unverified in that environment** | blocked live checks |
| Query parsing edge cases (quotes, C++/C#/.NET, Unicode, ambiguity) | **Verified offline** | tests/test_query_text.py |
| Ranking quality measurably improved, no regression | **Verified (MRR +0.10, nDCG@5 +0.074)** | table above |
| Concurrent variants + dedup + versioned pages preserved | **Verified offline** | tests/test_search_hardening.py, tests/test_search_ranking.py |
| Meaningful no-results / low-confidence responses | **Verified offline** | `filtered_out`, `summary`, tests |
| Consistent `max_chars` on every read path | **Verified offline** | facade test |
| HTTP errors / 403/404 / bot checks / rate limits / soft 404s | **Verified offline** | tests/test_web_reader.py |
| SSRF / redirect / DNS-rebinding protection | **Verified offline (local server + simulated DNS)** | tests/test_ssrf_fetch.py |
| Size limits / deadlines / slow-drip | **Verified offline** | tests/test_web_reader.py |
| Safe engine install (verify, retry, rollback, status) | **Logic verified offline; real download unreachable there** | tests/test_engine_install.py |
| Query length + control-char sanitization | **Verified offline + CLI** | adversarial matrix |
| Honest verification distinguishing local vs live | **Implemented** | interface verification |

## What remained unverified there (network-restricted)

1. **Live web search** (DuckDuckGo HTML/lite, Bing) — parsers are
   fixture-tested; live markup was not seen.
2. **Remote reader (`r.jina.ai`)** — hardening tested with fakes; the live
   service was unreachable.
3. **Browser-engine binary** — release exists (verified via the GitHub API)
   but the asset CDN was unreachable; the binary was not downloaded or run.
4. **Live integration tests** (`tests/test_integration_live.py`) — all
   skipped without egress.
5. **A real coding-agent MCP session** — no external agent available.
6. **Windows and macOS paths** — no such machines available.

## Remaining risks

- Live-only behaviors (engine blocking rates, reader availability, markup
  drift) can only be observed with real Internet access.
- Injection flagging remains heuristic (English phrasings); advisory, not a
  boundary.
- Authority scoring is a coarse suffix table (documented).
- The ranking benchmark is synthetic; it measures the scorer, not the live
  web.
- Without an operator-supplied checksum, engine integrity rests on HTTPS +
  the startup probe (upstream publishes no checksum); the installer says so.

## Reproduce

```bash
./scripts/setup.sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check --select E9,F,B .
.venv/bin/mypy iralens
.venv/bin/python scripts/ranking_benchmark.py     # MRR/nDCG table
.venv/bin/python scripts/verify_interfaces.py     # PASS/FAIL/BLOCKED verdicts
```
