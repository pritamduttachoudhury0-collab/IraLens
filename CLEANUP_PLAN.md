# Cleanup plan — 2026-10-16

Disposition of every file and folder before the cleanup starts. Actions are
applied only with `git mv` / `git rm`, in small commits. The pre-cleanup state
is preserved on GitHub as the annotated tag `archive/pre-cleanup` (this
session is fixed to one branch, so a tag is used as the recovery point).

Gates that must pass after every structural step: `pytest -q` (428 passed, 14
skipped baseline), `ruff check --select E9,F,B .`, `mypy iralens`.

## Root

| File | Action | Notes |
|---|---|---|
| LICENSE | KEEP | Apache-2.0 text |
| NOTICE | KEEP | copyright + attributions |
| LICENSE_AUDIT.md | MOVE → docs/audits/license-audit.md | license audit evidence; fix README link |
| README.md | REWRITE | premium finished feel; measured-results table with dates + evidence links |
| CHANGELOG.md | KEEP + extend | record cleanup decisions and summary |
| pyproject.toml | KEEP, edit | bump version 0.2.0 → 0.2.1 (v0.2.0 tag already exists on GitHub; re-tagging v0.1.0 would rewind versions); drop D-083 comment ref |
| MANIFEST.in | KEEP, edit | adjust doc includes after moves |
| .gitignore | KEEP, extend | common editor/OS files, `results` naming collision check |
| .editorconfig | NEW | |
| CONTRIBUTING.md | NEW (absorbs AGENTS.md durable rules) | |
| SECURITY.md | NEW | reporting policy, boundaries |
| CODE_OF_CONDUCT.md | NEW | Contributor Covenant |
| CLEANUP_PLAN.md | DELETE at end | this file |
| AGENTS.md | MERGE → CONTRIBUTING.md, DELETE | contains build-session narrative and D-refs |
| ARCHITECTURE.md | REWRITE | describe current architecture; drop the two-source build study (attribution stays in NOTICE) |
| DECISIONS.md | REWRITE | "Why we chose X" design notes, ordered, no D-numbers or build language; facts kept |
| BENCHMARKS.md | KEEP at root, CLEAN | runner appends rows to this exact path (benchmarks/run_benchmark.py:883); rewrite the run log in product language |
| KNOWN_LIMITATIONS.md | KEEP at root, REWRITE | protected doc; same limits in product language; keep dated verification records, drop build story |
| IRA-LENS-FINAL-PRODUCTION-ACCEPTANCE-AUDIT.md | MOVE → docs/audits/production-acceptance-audit-2026-10-09.md | audit evidence; strip build words, fix links |
| Dockerfile | KEEP | |
| docker-compose.yml | KEEP | |
| .dockerignore | KEEP | |

## iralens/ — KEEP all (working product code)

- Edit comments/docstrings that reference D-numbers or build narrative
  (`settings.py`, `sources/search.py`, `cli.py`, `fetch.py`, `extract.py`,
  `engine/install.py`, etc.). Behavior unchanged.
- `content_guard.py` comment: `docs/THREAT_MODEL.md` → renamed doc path.
- Bump `__version__` to 0.2.1.

## tests/ — KEEP all (428 passing suite; live tests auto-skip without egress)

## benchmarks/ — KEEP all (frozen benchmark set is protected)
- `benchmarks/README.md`: strip build words.
- No corpus/threshold edits (freeze protected).

## scripts/ — KEEP all
- `daily_watch.py`, `verify_interfaces.py`, `ranking_benchmark.py`,
  `live_github_research.py`, `triage_reads.py`: strip build words in
  docstrings (`sandbox` → neutral wording). `setup.sh`/`setup.ps1` keep.
- `triage_reads.py` stays: it regenerates the READ_FAILURES triage and is tested
  (`test_read_triage.py`).

## tools/ — DELETE
- `tools/install_engine.py` is a thin duplicate of the packaged
  `iralens install-engine` command; update MANIFEST.in and engine/locate.py
  comment that mentions `tools/engine` (vendored-engine lookup path stays in
  code; only the comment is updated if needed — check tests first).

## docs/

| File | Action |
|---|---|
| API.md | MOVE → docs/api.md, opens with one plain sentence |
| SEARCH.md | MOVE → docs/search.md; merge SOURCE_POLICY.md content as "Source policy" section |
| SOURCE_POLICY.md | MERGE into docs/search.md, DELETE file |
| RESEARCH.md | MOVE → docs/research.md |
| THREAT_MODEL.md | MOVE → docs/security.md |
| OPERATIONS.md | MOVE → docs/deployment.md; absorb deploy/searxng/README.md essentials |
| READ_FAILURES.md | MOVE → docs/audits/page-read-triage-2026-10-14.md; audit evidence |
| PHASE_STATUS.md | MERGE verified metrics into README measured table / audit docs, DELETE (build-phase narrative) |
| CAPABILITY_MAP.md | MERGE capability verification status into docs/getting-started.md, DELETE (build-assembly narrative) |
| docs/README.md | NEW — index of the doc set |
| docs/getting-started.md | NEW — install, verify, capability status |
| docs/reading-pages-and-documents.md | NEW — reading incl. PDF/OCR, from README + RELIABILITY content |
| docs/RELIABILITY.md | DELETE after folding unique content into docs/reading-pages-and-documents.md + docs/search.md (cache/circuit taxonomy) — check test references first |
| docs/audits/ | NEW — audit evidence home (+ license-audit.md, acceptance audit, paid-search audit, read triage, final-verification.md) |

## results/ — DELETE directory
- `results/2026-10-14/REPORT.md` and `results/2026-10-16/REPORT.md` are byte-identical
  (verified with diff). KEEP one copy → MOVE to
  docs/audits/paid-search-replacement-audit-2026-10-14.md; fix its dead raw-file
  links (raw artifacts were never committed — annotate, do not link). Remove the
  duplicate and the now-empty folders.

## deploy/searxng/ — KEEP compose + settings; README shrinks to pointer at docs/deployment.md

## .github/
- KEEP ci.yml, daily-watch.yml.
- NEW ISSUE_TEMPLATE/bug_report.md, ISSUE_TEMPLATE/feature_request.md,
  PULL_REQUEST_TEMPLATE.md.

## Branch/workflow adaptations (session constraints, recorded in CHANGELOG)
- This session is fixed to branch `arena/aa0e613f-iralens`. All merges of
  working branches (only `arena/91334bd2-iralens` had unique commits; the
  other six are ancestors of main) land on the session branch and reach `main`
  through a normal pull-request merge. Not rewriting any history.
- `archive/pre-cleanup` pushed as an annotated tag, not a branch.
- Manual dispatch of daily-watch.yml is denied by the token (HTTP 403); the
  workflow is on main with `workflow_dispatch` + schedule — the script itself
  is verified locally instead.
- No `audit/paid-search-replacement-20261014-phase1` branch exists on GitHub
  anymore; nothing to merge from it. Its report survives in results/.
