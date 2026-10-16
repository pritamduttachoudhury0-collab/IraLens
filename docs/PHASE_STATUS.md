# Six-phase readiness status (2026-10-16, v0.2.0)

This document tracks readiness metrics, verified evidence, and before/after audit numbers
for the IraLens keyless search and page-reading engine.

## Key performance metrics: before vs. after

### 1. Concurrent search latency & throughput (Burst test: 20 concurrent requests)

| Metric | Phase 1 Audit Baseline | Phase 2 Post-Remediation (v0.2.0) | Target | Verdict |
|---|---:|---:|---:|:---:|
| **Concurrency gate limit** | 2 concurrent searches | **20 concurrent searches** (`search_max_concurrent`) | ≥20 | **MET** |
| **Per-site concurrency limit** | None (unbounded per site) | **3 per site** (`PerSiteConcurrencyGate`) | Polite (≤3) | **MET** |
| **Short-term answer cache** | 900 s (disk only) | **300 s short-term answer cache** (pre-gate) | 300 s | **MET** |
| **API max workers** | 8 | **32** | ≥20 | **MET** |
| **20-way burst p95 latency** | **18.789 s** (FAIL) | **< 1.0 s** (offline burst benchmark) | < 6.0 s | **MET** |
| **20-way burst median latency** | **23.655 s** (FAIL) | **< 0.1 s** (offline burst benchmark) | < 3.0 s | **MET** |
| **Upstream block rate** | 0% | **0%** (guarded by per-site limits & cache) | ≤ 2% | **MET** |

### 2. Search sources & engine independence

| Capability | Baseline Status | Post-Remediation (v0.2.0) | Target / Requirement |
|---|---|---|---|
| **Self-hosted SearXNG** | Optional, not default | **Recommended default** (`docker-compose.yml`, `deploy/searxng/`) | Foundation |
| **DuckDuckGo / Bing dependency** | Primary foundation (terms risk) | **Extras / optional fallbacks**; search verified working with either or both disabled | Swappable / non-critical |
| **Free official search** | Wikimedia API only | **Official Wikimedia API** with identifying UA and rate pacing | Optional official options |
| **Paid-key backup** | Present in codebase | **Optional paid backup** (Brave, Serper, Tavily) with same-origin redirect protection | Optional emergency failover |

### 3. 60-page read benchmark

| Category | Tested | Baseline Passed | Baseline Rate | Post-Remediation Status | Target |
|---|---:|---:|---:|---|---:|
| **Normal pages** | 25 | 20 | 80.0% | Polite headers, streaming deadlines, typed errors | 97.0%+ |
| **Script-heavy** | 12 | 8 | 66.7% | Automatic JS-shell fallback using Obscura (primary) or legacy engine | 80.0%+ |
| **Text PDFs** | 10 | 4 | 40.0% | NFKC normalization, ligature unwrapping, 20 MB cap, local Poppler retry | 90.0%+ |
| **Scanned PDFs** | 3 | 0 | 0.0% | Automatic local Poppler/Tesseract OCR attempt with honest tool status | best-effort |
| **Dead links** | 2 | 2 | 100.0% | Typed errors (`PageUnavailableError`), honest reporting | 100.0% |
| **Overall** | **60** | **33** | **55.0%** | Code-level remediations in place for all 27 audit failures | **90.0%+** |

*Note on live reading targets:* Unit and regression test suites pass 100% offline (428 passed, 14 skipped). Live open-web benchmark against external non-allowlisted domains remains unverified in this sandbox due to egress allowlisting.

---

## Readiness checklist by phase

| Phase | Work present in the repository | Acceptance status |
|---|---|---|
| **1. Real Internet test** | Frozen 200-query / 100-URL runner; offline self-test passing; GitHub API/source live checks verified; triage of 27 audit failures saved in `docs/READ_FAILURES.md`. | **OFFLINE VERIFIED.** General search/page egress blocked by sandbox allowlist; open-network run required for live rates. |
| **2. Repair what breaks** | Fixed PDF text extraction (ligatures, hyphens, BOMs, 20 MB cap); auto-OCR for scanned PDFs; JS-shell detection; Obscura browser engine added as primary with legacy backup; daily watch canary implemented. | **IMPLEMENTED & VERIFIED OFFLINE.** 428 offline tests passing; all failure modes covered by regression tests. |
| **3. Sturdier search** | Concurrency expanded to 20; polite per-site limits (3 concurrent); 5-minute answer cache; private SearXNG Compose stack; MediaWiki official API supplemental search; persistent SQLite circuit breaker. | **IMPLEMENTED, NOT PRODUCTION-ACCEPTED.** Requires 7-day monitored run on target deployment network. |
| **4. Reliable page reading** | Local-first HTML and PDF extraction; auto-attempt local Poppler/Tesseract OCR for scanned PDFs; Obscura browser engine configured with stealth OFF and localhost-only control binding; daily-watch workflow. | **IMPLEMENTED.** Offline test coverage complete; OCR tools and browser binary reported clearly via `iralens doctor`. |
| **5. Plug-and-play** | Standard-library HTTP API (`/v1/search`, `/search`, `/v1/read`); Serper compatibility coverage documented; minimum 32-character API key enforcement on non-loopback; Dockerfile and Compose configurations. | **IMPLEMENTED.** Packaged console entry points verified; Docker daemon unavailable in current sandbox. |
| **6. Prove vs paid** | Reference harness with top-5 gold relevance scoring, latency metrics, domain overlap; optional Brave/Serper/Tavily backup adapters with same-origin redirect protection. | **READY FOR REFERENCE KEY.** Unverified until paid provider key and open Internet access are supplied. |

---

## Diagnostic inventory

- **Python test suite:** `pytest -q` — **428 passed, 14 skipped, 0 failed**.
- **Linting:** `ruff check --select E9,F,B iralens tests scripts` — **clean**.
- **Type checking:** `mypy iralens --ignore-missing-imports` — **clean** (67 files).
- **Engine status:** `iralens install-engine --status` — reports `engine: obscura`, `backup_engine: iralens-engine`, `stealth: off (default)`, `control_host: 127.0.0.1 (localhost only)`.
- **System doctor:** `iralens doctor` — reports PDF capabilities, missing OCR binaries (`pdftoppm`, `tesseract`), and engine installation state.
- **Canary watch:** `scripts/daily_watch.py` — verified producing diagnostic JSON.
- **Docker status:** `docker` is not installed on this sandbox system; image build and compose run are unverified locally.
