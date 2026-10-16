# Changelog

All notable changes to this project are documented here, newest first.

## 0.2.1 — 2026-10-16

The cleanup release: one polished repository, product-language docs, and a
fully verified feature walk. No feature was removed; one routing bug found
during final verification was fixed.

### Fixed

- The CI workflow file is now valid YAML (an unquoted step name had made it
  unloadable, so the gate had never executed); CI passes on Python 3.10 and
  3.12.
- `open()` / `POST /v1/read` now fall back to the generic web reader for
  github.com URLs that are not `/<owner>/<repo>` (the GitHub source reports
  the shape as `OperationUnsupportedError` instead of a generic extraction
  failure, which `open()` treats as fall-through). Regression test added.

### Changed (structure and docs)

- Audit evidence moved to `docs/audits/`: license audit, 2026-10-09
  production acceptance audit, 2026-10-14 paid-search replacement audit,
  page-read failure triage, and the final verification report.
- Consolidated the documentation set with an index (`docs/README.md`):
  getting-started, search (absorbs the reliability and source-policy
  guides), reading-pages-and-documents, research, api, security, deployment.
- Rewrote `README.md` (measured-results table limited to dated, evidenced
  numbers; everything else labeled not yet verified), `DECISIONS.md` (design
  notes without process language), `KNOWN_LIMITATIONS.md`,
  `ARCHITECTURE.md`, and `BENCHMARKS.md`.
- Added `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  `.editorconfig`, and GitHub issue/pull-request templates.
- Version is consistent at `0.2.1` in packaging, runtime, MCP handshake, and
  badges. (Tagged `v0.2.1`, not `v0.1.0`, because `v0.2.0` was already
  released and re-tagging a lower version would rewind the line.)

### Removed

- `results/2026-10-16/REPORT.md` — byte-identical duplicate of the
  2026-10-14 audit report (the surviving copy is in `docs/audits/`).
- `tools/install_engine.py` — thin duplicate of the packaged
  `iralens install-engine` command.
- `docs/PHASE_STATUS.md`, `docs/CAPABILITY_MAP.md` — process-tracking
  documents; their verified numbers live in the README measured table and
  `docs/getting-started.md`'s capability status.
- `AGENTS.md` — its durable rules moved into `CONTRIBUTING.md`.

### Housekeeping notes (release process)

- Merged the one working branch that still had unmerged work (cleanup
  verification notes); all other working branches were already part of
  `main`. The former paid-search audit branch no longer exists on GitHub;
  its report is the document in `docs/audits/`.
- Pre-cleanup safety point: annotated tag `archive/pre-cleanup` on the
  pre-cleanup `main` tip (a fixed-branch session cannot push a branch, so a
  tag serves as the recovery point).
- Changes reach `main` through a normal pull-request merge; history is
  untouched.
- Manual dispatch of `daily-watch.yml` was refused by the automation token
  (HTTP 403); the script itself was verified locally (2/2 live reads) and
  the schedule stands. Editing repository metadata (topics, description)
  was refused the same way; the existing repository description is kept and
  topics can be added in the repo settings.
- Release tagged `v0.2.1` on the merged `main`; the eight now-merged working
  branches were deleted only after the `archive/pre-cleanup` tag was
  confirmed on GitHub.

### Verification (full record: `docs/audits/final-verification.md`)

- Clean-environment install from the README: pass.
- Tests: **429 passed, 14 skipped, 0 failed** (skips are live-network and
  browser-engine tests, each with a stated reason); ruff (CI selection)
  clean; mypy clean across 67 source files.
- Feature walk: 23/23 runnable checks pass, including live GitHub reads,
  GitHub search, MCP (49 tools, three calls), the HTTP API with the
  paid-style swap, research over live GitHub content, PDF extraction,
  health memory, caching, and the daily-watch script (2/2).
- Honestly unverified here and labeled as such: live search-engine
  production (last measured 2026-10-14: DuckDuckGo 15/15, Bing 15/15), the
  frozen live benchmark, OCR execution, browser-engine execution, Docker,
  Windows setup, paid-provider parity, seven-day watch record.

## Unreleased (pre-cleanup record, 2026-10-16)

- Baseline recorded before the cleanup: 428 passed, 14 skipped; mypy clean
  on 67 files; ruff CI selection (E9,F,B) clean.
