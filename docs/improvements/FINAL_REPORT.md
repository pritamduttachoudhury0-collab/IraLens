# Evidence-driven IraLens improvements — final report

**Date:** 2026-10-17

**Baseline:** IraLens v0.2.1 at `dabf59a38114b4680b2d013b74b74cdef71f7621`

**Implementation branch:** `arena/f3446c85-iralens` (Arena session branch; this repository session cannot switch to `main`)

**Purpose:** address defects D1–D8 from the attached benchmark/improvement instructions while preserving a zero-mandatory-fee, local-first architecture.

## 1. Summary and scope

Implemented deterministic, no-new-dependency improvements to search ranking, intent/jurisdiction handling, non-Latin script handling, response diagnostics, date provenance, and result URL/security metadata. Added a keyless Mojeek backend, **registered but opt-in and not part of the default chain**, because neither its live results page nor its current service terms could be checked here.

The improvement prompt's benchmark raw captures are **not present in this checkout** (`benchmark/raw/` absent). The 1,300-query report was available as an attached document, but the raw R5 rows needed to replay Q283/Q297/Q267 were not. The workspace also has no general web egress. Therefore the complete live re-run, before/after holdout P@5, human grading, and engine soak measurements are **NOT MEASURED**. No live result, success, or quality gain is claimed.

The 300-query holdout was written and hashed before code tuning. Exact-text overlap with the repository's own `benchmarks/queries.json` is zero; that is not proof of de-duplication against the absent 1,300-query query files. No holdout searches were run.

## 2. Implemented

- **D1 — answer density/specificity:** relevance now treats title and snippet coverage separately, downweights generic hub/disambiguation/homepage/dictionary shapes, and uses specific source context. A regression fixture explicitly reconstructs the v0.2.1 formula: its generic page scores above the answer-specific page, while the new behavior orders the specific page first. Synthetic 20-case ranking benchmark, measured with the checked-in harness: legacy MRR **0.8750**, current **0.9250**; legacy nDCG@5 **0.9077**, current **0.9446**. These synthetic numbers are not live search quality.
- **D2 — answer type and jurisdiction:** deterministic lexical classification (procedure, statistic, current event, error, comparison, definition/entity lookup, general) supplies soft official-domain/source-type priors and a jurisdiction-biased query variant. Default authority recognizes Indian `.gov.in`, `.nic.in`, and `.ac.in` domains. It does not hard-filter and can misclassify.
- **D3 — scripts/languages:** NFC normalization, bigram tokens for Thai/Lao/Khmer/Myanmar as well as CJK, script detection for major Unicode blocks, a best-effort Yoruba Latin hint, and Bing/Wikipedia language routing. Wikipedia tries the detected edition then English if no local hits. This is script-aware behavior, not a full language-identification model.
- **D4 — degradation visibility:** serialized search responses retain the existing `degraded` boolean and now include answered, failed and skipped engines plus skip reasons. Existing persistent half-open breaker was inspected and retained; no duplicate breaker added.
- **D5 — Wikipedia role:** Wikipedia remains unrestricted for definitions/entity lookups; non-definitional intents cap it at one row, and hubs are demoted. Results are not padded with further hubs if the cap leaves a short list.
- **D6 — shortfall:** fewer-than-requested results carry `capped`, `deduped`, `filtered`, or `exhausted` reason in the response and summary; results are never padded.
- **D7 — date provenance:** cheap offline extraction handles unambiguous ISO/written dates in snippets and date-formatted URL paths; `date_evidence` records the source. No page fetching was added to search.
- **D8 — security metadata:** known Google Translate and `r.jina.ai` wrappers are unwrapped before validation/canonicalization. Test/staging/dev/local/IP hosts are flagged and demoted, not silently removed. Search results preserve injection/test-host flags into the structured response. Existing research path scans retrieved content and carries flags; no page text is treated as tool instructions.
- **Phase 4 extraction scaffold:** added a 100-page expected-span fixture evaluator. Its pages are synthetic generated HTML, so it is only an extractor smoke test—not the requested real-world extraction benchmark.
- **Phase 0 instrumentation:** added a factor dump helper and an offline structural evaluator for the frozen holdout.

No vendored Obscura file was changed. The Obscura licence position and attribution are unchanged; no third-party code was added. No mandatory dependency or paid API was added.

## 3. Files changed

- `iralens/search/ranking.py`, `intent.py`, `language.py`, `querytext.py`, `engine.py`, `schema.py`, `debug.py`, `freshness_extract.py`, `urls.py`: ranking/intent, Unicode, response diagnostics, date extraction, URL unwrapping and host safety.
- `iralens/search/engines/wikipedia.py`, `bing.py`, `engines/__init__.py`, new `engines/mojeek.py`: language routing and opt-in engine registration.
- `iralens/settings.py`: Indian government/accredited-domain source weights.
- `tests/regression/test_benchmark_failures.py`, `tests/test_mojeek.py`, plus ranking/Wikipedia tests: regression and failure-injection coverage.
- `benchmark/holdout/`: frozen 300-query JSONL, its builder/readme/hash, offline structural evaluator, 100 synthetic extraction pages and evaluator.
- `docs/improvements/phase-0.md` through `phase-5.md`, this report, and updates to `README.md`, `KNOWN_LIMITATIONS.md`, `BENCHMARKS.md`, `docs/search.md`, and `DECISIONS.md`.

## 4. Verification and actual results

Executed from repository root with Python 3.11.2 / `.venv`:

| Check | Result |
|---|---|
| `bash scripts/setup.sh` | exit 0; installs completed; `iralens doctor` OK; version 0.2.1 |
| `.venv/bin/python -m pytest -q` | **463 passed, 14 skipped, 0 failed** in 41.05 s. Skips are tests requiring live network. |
| `.venv/bin/ruff check --select E9,F,B .` | **All checks passed** |
| `.venv/bin/mypy iralens` | **Success, no issues in 79 source files** |
| `.venv/bin/python benchmark/holdout/evaluate_offline.py` | 300 unique IDs, exactly 30/category, 0 empty term sets; reports intent/script counts only. P@5 explicitly **NOT MEASURED**. |
| `.venv/bin/python benchmark/holdout/evaluate_extraction.py` | **100/100** expected spans present on synthetic fixture HTML. Live/real-page extraction **NOT MEASURED**. |
| `.venv/bin/python scripts/ranking_benchmark.py` | legacy MRR 0.8750 → current 0.9250; legacy nDCG@5 0.9077 → current 0.9446, 20 synthetic cases. |
| `pip wheel` (isolated build, `--no-deps`) | baseline archive wheel 1,180,881 bytes; current wheel 1,193,585 bytes; **+12,704 bytes**. Same isolated build command; no new mandatory dependencies. |
| GitHub Actions PR checks | The first run passed Python 3.10 but failed Python 3.12 at the Offline test suite step; the Actions log endpoint returned EOF, so the failing assertion could not be identified. A subsequent run on the final documentation commit passed both Python 3.10 and 3.12. The initial failure was not reproduced; the latest required checks are green. |

A first packaging attempt with `--no-build-isolation` failed because the ambient setuptools was too old for this project's PEP 621 `license` metadata; the isolated build succeeded and is the reported build result.

Not run / not measurable here: live search quality on the frozen holdout, human grading (one or two graders), live Mojeek or post-change DDG/Bing checks, 30-minute blocked-engine soak/recovery, real 100-page extraction, overall freshness coverage, production p95 latency/RSS comparison, and deployment on an open network. No performance or relevance gain is inferred from unit tests.

## 5. Holdout and extraction artifacts

- `benchmark/holdout/queries_holdout.jsonl`: **300** pre-tuning queries, 30/category; SHA-256 `2c96ebe8ba65b3a53278b7f9e4b411da08a972e8412ffb037ada595d28306aa5`.
- `benchmark/holdout/extraction_pages.jsonl`: **100 synthetic pages**; SHA-256 `16d801a9a206b50f125cb00ba74a2e69133e7e4e64a53dbba7313e7a33832920`.
- Commands: `.venv/bin/python benchmark/holdout/evaluate_offline.py` and `.venv/bin/python benchmark/holdout/evaluate_extraction.py`.
- No query result set is included because live engines were unreachable. Do not treat the holdout as passed.

## 6. Remaining limitations / acceptance gaps

1. Live holdout proxy P@5 ≥0.40, category floors, doubling of categories 2/7/8/9, and Wikipedia ≤15% are **NOT MEASURED**; acceptance is unverified.
2. No independent human-graded sample of 100 IraLens queries exists. The 1,300-query benchmark itself warns of ±0.06 grader drift; no human quality claim is made.
3. Degraded behavior is covered with deterministic fakes, not an injected network block or a 30-minute live soak. Automatic recovery timing is unverified.
4. Mojeek parser is fixture-only and terms are not reviewed here; it stays opt-in.
5. Multilingual handling is incomplete for Latin-script language identification and translation quality. It cannot guarantee zero unrelated-language results.
6. Date extraction coverage and false-date rate are unmeasured; date patterns are intentionally conservative.
7. Extraction score 100/100 covers generated simple HTML only, not 100 independent web pages.
8. Arena latency/resources, IraLens post-change end-to-end latency/RSS under the original 2-vCPU conditions, recall, page-body extraction quality and citation correctness are unmeasured.
9. No change was made to the vendored browser engine; its licence/attribution remains as before.

## 7. Git and delivery

All implementation commits are on the session's fixed branch `arena/f3446c85-iralens`; no other branch or worktree was created. PR #10 targets `main` and is open at https://github.com/pritamduttachoudhury0-collab/IraLens/pull/10. The initial Python 3.12 run failed but could not be diagnosed from its inaccessible log; a subsequent CI run on the latest commit passed both Python 3.10 and 3.12. The changes are committed and pushed to the fixed session branch. At report authoring the PR remains open; merge status is reported in the final response.
