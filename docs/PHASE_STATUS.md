# Six-phase readiness status (2026-10-16)

This document tracks readiness metrics, verified evidence, and before/after audit numbers
for the IraLens keyless search and page-reading engine.

## 60-page read benchmark: before vs. after comparison

### Audit baseline (Phase 1 audit, `results/2026-10-16/REPORT.md`)

| Category | Tested | Passed | Baseline Rate | Target | Gap |
|---|---:|---:|---:|---:|---:|
| **Normal pages** | 25 | 20 | 80.0% | 97.0%+ | -17.0% |
| **Script-heavy** | 12 | 8 | 66.7% | 80.0%+ | -13.3% |
| **Text PDFs** | 10 | 4 | 40.0% | 90.0%+ | -50.0% |
| **Scanned PDFs** | 3 | 0 | 0.0% | best-effort | -100.0% |
| **Dead links** | 2 | 2 | 100.0% | 100.0% | met |
| **Overall** | **60** | **33** | **55.0%** | **90.0%+** | **-35.0%** |

### Post-remediation status (Phase 2 fixes: D-095..D-098)

| Component / Test | Old Engine (`iralens-engine`) | Obscura (`obscura` v0.2.4) | Notes / Evidence |
|---|---|---|---|
| **Text PDF reading** | Bounded 20 MB cap; NFKC & ligature normalization; local `pdftotext` retry | Same (shared extraction pipeline) | Verified offline: `tests/test_pdf_text_quality.py` (5/5 passed). Online open-web rate unverified due to sandbox network egress. |
| **Scanned PDF reading** | Auto-attempt local OCR; explicit tool reporting | Same (shared extraction pipeline) | Verified offline: `tests/test_pdf_autodetect.py` (2/2 passed). Reports `pdf_ocr_support_missing` when Poppler/Tesseract absent. |
| **Script-heavy fallback** | Backup engine in `locate.py` | Primary optional engine, pinned to v0.2.4 | Stealth OFF by default; control host bound to `127.0.0.1` (localhost only). Subprocess binary installation gated on release asset egress. |
| **Full test suite** | **423 passed, 14 skipped** | **423 passed, 14 skipped** | Clean pytest run, 0 failures. Ruff (E9,F,B) clean; mypy (67 modules) clean. |
| **Daily watch canary** | 2/2 passed (local run) | 2/2 passed (local run) | `scripts/daily_watch.py` runs against GitHub endpoints, writes JSON diagnostic report. |

### Honest assessment against targets

- **Normal pages (Target: 97%+):** Implementation hardened with polite headers, realistic User-Agent, streaming deadlines, and typed error responses. Live open-web benchmark cannot be run from this sandbox because external domain egress is blocked.
- **Script-heavy pages (Target: 80%+):** Implemented automatic JS-shell fallback in `auto` mode (D-081) using Obscura as primary engine with legacy engine backup. Live execution requires installed browser binary.
- **Text PDFs (Target: 90%+):** Root causes resolved via NFKC normalization, ligature and line-wrap hyphen unbreaking, BOM handling in magic-byte detection, separate 20 MB download cap, and local `pdftotext -layout` sparse-text retry (D-097). Offline suite passes 100%.
- **Overall (Target: 90%+):** Code fixes address all 27 audit failure causes (11 `not_enough_text`, 5 `blocked`, 3 `too_slow`, 2 `wrong_format`, 6 `other`). Full acceptance requires running the frozen 60-page suite in an open-network environment where target sites are reachable.

---

## Readiness checklist by phase

| Phase | Work present in the repository | Acceptance status |
|---|---|---|
| **1. Real Internet test** | Frozen 200-query / 100-URL runner; offline self-test passing; GitHub API/source live checks verified; triage of 27 audit failures saved in `docs/READ_FAILURES.md`. | **OFFLINE VERIFIED.** General search/page egress blocked by sandbox allowlist; open-network run required for live rates. |
| **2. Repair what breaks** | Fixed PDF text extraction (ligatures, hyphens, BOMs, 20 MB cap); auto-OCR for scanned PDFs; JS-shell detection; Obscura browser engine added as primary with legacy backup; daily watch canary implemented. | **IMPLEMENTED & VERIFIED OFFLINE.** 423 offline tests passing; all failure modes covered by regression tests. |
| **3. Sturdier search** | Private SearXNG Compose stack; MediaWiki official API supplemental search; persistent SQLite circuit breaker with exponential cooldowns and CAPTCHA backoff. | **IMPLEMENTED, NOT PRODUCTION-ACCEPTED.** Requires 7-day monitored run on target deployment network. |
| **4. Reliable page reading** | Local-first HTML and PDF extraction; auto-attempt local Poppler/Tesseract OCR for scanned PDFs; Obscura browser engine configured with stealth OFF and localhost-only control binding; daily-watch workflow. | **IMPLEMENTED.** Offline test coverage complete; OCR tools and browser binary reported clearly via `iralens doctor`. |
| **5. Plug-and-play** | Standard-library HTTP API (`/v1/search`, `/search`, `/v1/read`); minimum 32-character API key enforcement on non-loopback; Dockerfile and Compose configurations. | **IMPLEMENTED.** Packaged console entry points verified; Docker daemon unavailable in current sandbox. |
| **6. Prove vs paid** | Reference harness with top-5 gold relevance scoring, latency metrics, domain overlap; optional Brave/Serper/Tavily backup adapters with same-origin redirect protection. | **READY FOR REFERENCE KEY.** Unverified until paid provider key and open Internet access are supplied. |

---

## Diagnostic inventory

- **Python test suite:** `pytest -q` — **423 passed, 14 skipped, 0 failed**.
- **Linting:** `ruff check --select E9,F,B iralens tests scripts` — **clean**.
- **Type checking:** `mypy iralens --ignore-missing-imports` — **clean** (67 files).
- **Engine status:** `iralens install-engine --status` — reports `engine: obscura`, `backup_engine: iralens-engine`, `stealth: off (default)`, `control_host: 127.0.0.1 (localhost only)`.
- **System doctor:** `iralens doctor` — reports PDF capabilities, missing OCR binaries (`pdftoppm`, `tesseract`), and engine installation state.
- **Canary watch:** `scripts/daily_watch.py` — verified producing diagnostic JSON.
