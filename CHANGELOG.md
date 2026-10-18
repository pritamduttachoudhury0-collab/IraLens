# Changelog

All notable changes to this project are documented here, newest first.

## 0.3.0 — 2026-10-18

Remediation of the independent audit of 2026-10-17, in three phases, plus a
fourth pass over the audit's own corpus. The complete error register
(E-01…E-44, the cross-audit ID map and the phase plan) is in
`docs/audit/ERROR_REGISTER.md`.

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

### Post-phase — retrieval quality: the `site:` path, the coverage metric, and the query form (E-36…E-38)

Three defects found by going back to the live providers rather than to the
captured baseline.

- **E-36 — the `site:` operator lost its path.** `site:github.com/scipy/scipy`
  was parsed as `site:github.com`, so a repository query became a whole-site
  search and the scope gate accepted any page on the host. The path is now
  parsed, enforced against the result URL, and survives tokenizing and
  reformulation. 324 of the 3,000 corpus queries (10.8%) carry a path scope.
- **E-37 — the answer-coverage metric judged the whole question.** A result
  was scored as failing to cover a question about a primary source when it
  did not contain the word "primary", and a page titled "GLOBLE" "covered" a
  question about global temperature on one long word. The metric now judges
  the question's *subject* — the request shape removed — and its
  *distinguishing* terms. Against ten hand-labelled real provider captures
  the old metric agreed with the human judgement in 6 of 10 and the new one
  in 10 of 10.
- **E-38 — only one query form was ever sent.** A long research question
  with a request shape is answered by a loose engine with its head term:
  "Welcome to Python.org" for a question about `asyncio.TaskGroup`, the
  nginx home page for a websocket question. A new `focus` reformulation
  strategy sends the question's subject on its own, **in addition to** the
  full question — the audit's "keep the full query; do not silently
  simplify it" is honoured literally. Measured on the loose engine, the
  on-target rate for "nginx reverse proxy websocket version specific known
  issue" goes from 0/10 to 7/7.

Two consequences followed from the same investigation. A *short* keyword
form is a liability — the two-word form of "What is the capital of Assam?" is
"capital assam", which returns 0 on-target results where the question
returns 10 — so keyword forms are only sent when they still carry at least
three content terms. And the audit's 45 manual relevance labels cannot be
used as ground truth: 17 of them are Wikipedia results whose title matches
the question's subject but whose snippet was empty before E-03, so the
reviewer was judging a page they could not see.

The subject split that `focus` depends on is measured, not asserted: the
corpus records the topic of every templated query, and the splitter
recovers **2,622 of 2,950 = 88.88%** of them exactly
(`benchmarks/measure_focus_split.py`). The request lexicon is derived from a
training half of the corpus and scored on a held-out half: **89.15%**
(`benchmarks/derive_request_markers.py`).

### Post-phase — four more defects, found in the corpus itself (E-39…E-42)

The audit bundle was rebuilt from the uploaded documents
(`benchmarks/extract_audit_bundle.py` recovers all 101 embedded files) and
every corpus query was re-examined against the result it actually received.

- **E-39 — a query that names a page was searched for, not read.** 130 of
  the 3,000 queries (4.3%) are a URL, sometimes followed by what the caller
  wants from that page. The engine tokenized the URL into words and searched
  for them, so `https://docs.python.org/3/` was answered with the Wikipedia
  article *HTTPS* and `https://react.dev/learn` with the article *Svelte*.
  A URL at the head of a query is now a **read** request: the page is
  fetched through the normal reader and returned, with its own text as the
  answer. A URL followed by a question, and a query naming two pages, are
  still searches. On the 9 of the 130 URLs the sandbox can reach, the
  coverage verdict went from `low` to `good` in 9 of 9 cases. Of the
  baseline's 1,620 low-coverage queries, 124 are URL queries, so the read
  path addresses 7.7% of that set directly.
- **E-40 — the request shape named a kind of source and the ranking ignored
  it.** "official documentation", "primary literature", "government
  guideline" say what kind of page would answer the question. The ranking
  now reads that and gives a page of the requested kind a bounded, additive
  bonus of 0.20 of the relevance scale — additive, so the kind of a page
  can never outrank a page that simply answers the question, and a bare
  homepage is never boosted. Calibrated against the captured set
  (`benchmarks/measure_shape_ranking.py --sweep`): at the shipped 0.20 the
  top result changes in 32 of the 1,250 questions that ask for a kind of
  source, 5 of them for the better and 3 for the worse.
- **E-41 — the backward scan for a request word fell through when it found
  none**, inventing a request shape out of whatever trailed ("… arXiv
  1706.03762" became the shape "1706.03762"). The corpus's topics are now
  recovered exactly in 2,622 of 2,950 (88.88%), up from 88.78%.
- **E-43 — a bare year in the request lexicon was hiding the request
  shape.** The learned phrase set contained "2026", and the high-precision
  matcher takes the longest known phrase at the end of a query, so every
  query ending in a year was split *at the year*: "India GDP official
  dataset download October 2026" became the shape "2026", which says
  nothing about the kind of source wanted. A year is a temporal qualifier,
  not a request: it is separable on its own, but it never shadows the
  request before it. Every templated query in the corpus now gets a shape
  (0 of 2,519 without one), and the questions that ask for a kind of source
  rise from 1,159 to 1,250. The corpus's topics are still recovered in
  2,622 of 2,950 (88.88%) and the lexicon still scores 89.15% held-out.
- **E-42 — the GitHub adapter answered a repository sub-page with the
  repository.** Its regex matched `github.com/<owner>/<repo>` anywhere in a
  URL, so `https://github.com/astral-sh/uv/issues` was read as the
  repository's README. A sub-page now falls through to the generic web
  reader, as the adapter's own error message always claimed.

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
code-caused failure, **2,955/3,000** with no defect at all, and **1,637/3,000**
under the strictest reading. It is a projection, not a measurement.

### Measured

- `pytest -q` — **639 passed, 14 skipped, 0 failed**.
- `ruff check --select E9,F,B .` — clean. `mypy iralens` — clean.
- `python benchmarks/acceptance_17.py` — **17/17**.
- `python benchmarks/run_scraping.py` — **12/12**.
- `python benchmarks/run_audit3000.py --offline-pipeline` — **14,262/14,262
  clean** over the frozen 3,000-query corpus.
- `python benchmarks/run_audit3000.py --replay <audit baseline>` — 31,264
  checks: **8,435 code-caused**, **5,223 external**, 17,606 clean.
- `python benchmarks/project_score.py` — **3,000/3,000** with no code-caused
  failure; **2,955/3,000** with no defect at all; **1,637/3,000** strict.
- `python benchmarks/measure_focus_split.py` — the subject split recovers
  **2,622 / 2,950 = 88.88%** of the corpus topics exactly.
- `python benchmarks/derive_request_markers.py` — the request lexicon, learned
  on a training half of the corpus, scores **89.15%** on the held-out half.
- `python benchmarks/measure_shape_ranking.py --top1` — the audit's own
  captured results, ordered by the old code and by the current code: the
  top result differs for **59 of 2,743** queries. The coverage verdict
  calls 6 of them better and 11 worse, and every one of those 11 replaces
  a portal homepage with a specific page about the subject — the verdict
  rewards a page that repeats the question's words, not one that answers
  it, so no net improvement is claimed from that number.
- `python benchmarks/measure_url_reads.py` — the corpus's 130 URL-anchored
  queries, asked again: **9 of the 9** that this machine can reach go from
  coverage `low` to `good`; the other 121 are blocked by the sandbox's
  egress policy.
- `python benchmarks/measure_shape_ranking.py --sweep` — the request-shape
  preference, swept against the captured 3,000-query set: at the shipped
  bonus of 0.20 the top result changes in 32 of the 1,250 questions that
  ask for a kind of source, 5 of them for the better and 3 for the worse.
- `python benchmarks/ab_query_forms.py` — over ten hand-labelled real
  provider captures: the `focus` form is **7/7** on-target where the loose
  engine returns **0/10** for the full question; the coverage metric agrees
  with the human labels in **10/10** captures (6/10 before E-37).
- The live 3,000-query run is **not measured** in the verification
  environment: it can reach package indexes and GitHub only, so the
  provider-side captchas and throttles could not be exercised. The runner
  reports `not_run` with the reason.

### Part 1e — one defect found by the release's own CI (E-44)

- **E-44 — a test replaced a shared object's engine in place.** The source
  registry keeps one instance per source, so the E-26 de-duplication test
  installed a fake search engine on the shared `web-search` source and the
  assignment outlived the test. A later *live* test then ran against the
  fake, "found" `https://a.example/shared` and failed trying to open it.
  The suite passed locally only because the live tests skip without a
  network; GitHub's runners have one, so CI is where it showed
  (1 failed, 650 passed). The test now uses `monkeypatch.setattr`, and an
  autouse fixture snapshots the whole source registry around every test so
  that no test can leave a shared object changed.

### Release engineering

- The release is **0.3.0** and the version is written in four places —
  `pyproject.toml`, `iralens/__init__.py`, the MCP `clientInfo` handshake
  and the README badge. A test asserts that all four agree
  (`tests/test_release.py::test_the_version_is_the_same_everywhere_it_is_written`),
  so a badge that lies about the build cannot be shipped.
- The sdist and the wheel are built with `python -m build`, checked with
  `twine check` (both **PASSED**), checksummed (`dist/SHA256SUMS`) and
  attached to the GitHub release. Both were installed into a clean virtual
  environment and reported `iralens 0.3.0`.
- `.github/workflows/publish.yml` publishes to PyPI with **trusted
  publishing** (a GitHub OIDC token exchanged for a short-lived PyPI upload
  token). No API token is stored in the repository; the one owner-side step
  is in `docs/releasing.md`.
- The newest merged code is installable without a clone:
  `pip install "iralens @ git+https://github.com/pritamduttachoudhury0-collab/IraLens.git"`.
  The README, the getting-started guide and the known-limitations list now
  say so, and no longer say the package "is not yet published to PyPI"
  without saying what to do instead.

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
