# Changelog

All notable changes to this project are documented here, newest first.

## Unreleased — 2026-10-18

Remediation of the independent audit of 2026-10-17, in three phases. The
complete error register (E-01…E-35, the cross-audit ID map and the phase
plan) is in `docs/audit/ERROR_REGISTER.md`.

### Phase A — Integrity & Truthfulness (E-01…E-13)

The system no longer reports things that did not happen: a typed
`RateLimitedError` instead of a mislabeled reader failure, a shared
mode-aware HTTP classifier, cache provenance that is threaded explicitly
instead of inherited from the previous page, `srprop=snippet` so Wikipedia
hits carry context, revision timestamps reported as `modified_at` instead of
as publication dates, a scrubbed and bounded `detail` in `to_dict()`, a
structural block classifier, a truncation notice inside the character
budget, atomic and private session state, and a central content scan on
every artifact that leaves the facade.

### Phase B — Completeness & Scale (E-14…E-27)

The `site:` operator survives tokenizing and reformulation and is enforced on
the results; pagination is real (`offset`, Bing `first`, DuckDuckGo/Mojeek
`s`, SearXNG `pageno`, Wikipedia `sroffset`); Reddit can list a subreddit
through the anonymous RSS transport; a first-class Indie Hackers source
replaces the dead `/groups` and topic routes; date filters can exclude
undated results; a process-wide per-host rate budget paces requests and
honours `Retry-After`; every search carries an answer-coverage verdict; an
engine whose optional prerequisite is missing is reported as *not
configured* rather than as a failure; provenance is the recorded `found_via`,
not the stale `last_query`; post metadata is typed (`PostRecord`);
de-duplication works across a sweep; RSS permalinks are canonical.

### Phase C — Extraction, Resilience, Safety & Test-Readiness (E-28…E-35)

Application shells are detected structurally (link density, sentence
density, menu blocks) instead of by length alone; the extractor selects the
page's main-content region; the vendored browser's rendered-text collector
no longer leaks script or style source and tolerates attribute access on a
non-element node; the reader remembers a renderer that is dead for a host;
reads report the effective URL and the redirect chain, and preserve the
query string; the three acceptance harnesses are in the repository; and the
documentation matches the measured behaviour.

### Post-phase — a defect in our own measurement (M-01)

Projecting the score exposed a fault in the harness this project wrote to
measure the audit. The `RL` predicate used a lexical term-overlap proxy and
charged every low-coverage query to the code; against the audit's own 45
manual relevance confirmations that proxy has precision 0.016 and recall
0.756. The audit's own S-RL entry says `root_status: "Unknown"` and names
only the *reporting* as ours. The family is now split: `RL` (the response
hid the low coverage → `code`, fixed by E-22) and `RLR` (the results are
weak → `unresolved`, not attributable). A post-fix run now reaches a
code-caused count of zero instead of sticking at 2,098. Pinned by
`tests/test_audit_attribution.py`.

`benchmarks/project_score.py` is the new projection tool: it replays the
audit's captured baseline, applies the documented fix semantics of each
register entry, and reports the per-query outcome with every number traced
to the entry that produces it. It reports **3,000/3,000** with no
code-caused failure, **2,955/3,000** with no defect at all, and **902/3,000**
under the strictest reading. It is a projection, not a measurement.

### Measured

- `pytest -q` — **584 passed, 14 skipped, 0 failed**.
- `ruff check --select E9,F,B .` — clean. `mypy iralens` — clean.
- `python benchmarks/acceptance_17.py` — **17/17**.
- `python benchmarks/run_scraping.py` — **12/12**.
- `python benchmarks/run_audit3000.py --offline-pipeline` — **15,156/15,156
  clean** over the frozen 3,000-query corpus.
- `python benchmarks/run_audit3000.py --replay <audit baseline>` — 31,264
  checks: **9,170 code-caused**, **5,223 external**, 16,871 clean.
- `python benchmarks/project_score.py` — **3,000/3,000** with no code-caused
  failure; **2,955/3,000** with no defect at all; **902/3,000** strict.
- The live 3,000-query run is **not measured** in the verification
  environment: it can reach package indexes and GitHub only, so the
  provider-side captchas and throttles could not be exercised. The runner
  reports `not_run` with the reason.

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
