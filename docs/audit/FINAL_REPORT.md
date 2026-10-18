# Final report — the five-document audit, remediated

**Date:** 2026-10-18 · **Branch:** `arena/c4fa8fce-iralens` · **Base commit:** `555dca7`

This is the closing document of the error audit of the five uploaded audit
files. It states what was found, what was fixed, what was measured, and what
could not be measured — and why.

---

## 1. What was audited

Five documents were read and checked against the actual source:

| # | document | what it is | verdict |
|---|---|---|---|
| 1 | `weakness.md` (147 KB) | a 30-failure weakness register with per-item evidence, priority and a fix queue | **behaviours largely TRUE**, but its "verbatim" code appendices are from a *different codebase* — 6 of the files it cites do not exist here, and its R6 harness would `ImportError` on import. Two §8 cross-references are mis-cited (W-0029 ↔ W-0024, W-0030 ↔ W-0025). |
| 2 | `weaknesses_part1…4.md` (84 MB) | the frozen 3,000-query dataset, the harness, the captured runs, the evidence and the final report | **authoritative and fully trustworthy.** The embedded baseline tarball is byte-identical to this tree (sha256 `aa8b590d…`), all 7,553 failure records match the index exactly, and §5–§13 consolidation changed nothing but two links. |
| 3–5 | the remaining three uploads | the source bundle, the frozen dataset and the render/validate scripts | consistent with #2; the scripts run and reproduce the report. |

The three-phase remediation plan, the full error register (E-01…E-35, the
audit-ID cross-map and the eleven de-duplications D-01…D-11) is in
[`ERROR_REGISTER.md`](ERROR_REGISTER.md). Nothing found is omitted: every
defect that survived verification is in the register with a file/line
pointer, and every register entry is implemented and regression-tested.

### A correction the audit's own tooling produced

The first extraction of the embedded files mis-parsed the 4-backtick fences and
swallowed `read-Q0013.json` inside `read-Q0009.json`, which made the file
look missing. With a corrected extractor (matching the *leading backtick
run* of the opening fence) the bundle yields **99/101** files, both declared
hashes match exactly, and the audit's own `validate_findings.py` runs and
reports **all 17 checks `true`** against the baseline. The two files that
remain unrecovered straddle a part boundary and are not needed by any check.
`read-Q0013.json` exists and is now in `tests/fixtures/audit/`.

---

## 2. The three phases, and what each one fixed

### Phase A — Integrity & Truthfulness (E-01 … E-13)

*Stop the system reporting false things.* A defect that lies corrupts every
later measurement, so this phase came first.

- **E-01** an origin 429 was labelled a reader failure → a typed
  `RateLimitedError` with `retry_after`.
- **E-02** a cached read inherited the *previous* page's URL and status
  (the audit's S-CACHE P1) → the retrieval metadata is now threaded
  explicitly through the read and cached with the body.
- **E-03/E-04** Wikipedia returned no snippets and labelled a *revision*
  timestamp as a publication date → `srprop=snippet|timestamp`, with
  `modified_at`/`date_kind` keeping the two apart.
- **E-05** `to_dict()` dropped `detail` → it is preserved, scrubbed and
  bounded to 500 characters.
- **E-06/E-08** HTTP status and block detection were mode-blind and
  substring-based → one shared, mode/layer-aware classifier and a structural
  block classifier.
- **E-07** extraction failures were untyped → `extraction_failed` everywhere.
- **E-09** the truncation notice pushed the content over the budget → the
  notice is inside the budget.
- **E-10/E-11** concurrent session saves raced on one temporary file, and
  state files were world-readable → unique temp file + `fcntl` lock +
  merge-on-write, `0600` files in a `0700` directory.
- **E-12** not every artifact was scanned → one central `scan_artifact()`.
- **E-13** the RSS fetch was not SSRF-hardened → `safe_urlopen` with a typed
  status.

### Phase B — Completeness & Scale (E-14 … E-27)

*Make the system find and return everything.*

- **E-14/E-15** `site:` was destroyed by the tokenizer and never enforced →
  the operator survives, is parsed out, and out-of-scope hits are dropped and
  counted.
- **E-16** no pagination anywhere → `SearchOptions.offset` plus Bing
  `first`, DuckDuckGo/Mojeek `s`, SearXNG `pageno`, Wikipedia `sroffset`.
- **E-17/E-18** Reddit could only read one URL and ignored the one
  transport that works anonymously → subreddit listing through `/r/<sub>/.rss`.
- **E-19** no Indie Hackers source → a first-class source built on what the
  site serves today (JSON-LD, microformats, the public product directory);
  the dead `/groups` and topic routes are not used.
- **E-20** date filters kept undated results → `exclude_unknown_dates`.
- **E-21** no shared per-host pacing → `HostRateLimiter` honours
  `Retry-After`.
- **E-22** a nonempty result set of filler was reported as an answer → every
  search carries a coverage verdict; filler is `low_coverage`, and research
  coverage counts domains that address the question.
- **E-23** the unconfigured `semantic-search` engine was in the default
  chain → *not configured* is now distinct from *failing*; it is dropped from
  the effective chain and reported in `engine_readiness`.
- **E-24** provenance came from the stale `last_query` → the recorded
  `found_via`.
- **E-25** post metadata was prose-only → typed `PostRecord`.
- **E-26** de-duplication was per-response → `exclude_urls` and a `sweep()`.
- **E-27** RSS permalinks were lossy and malformed entries vanished →
  canonical permalinks, GUID fallback, reported entry warnings.

### Phase C — Extraction, Resilience, Safety & Test-Readiness (E-28 … E-35)

*Extract the right text, survive hostile pages, and make the whole thing
verifiable.*

- **E-28** only explicit JS notices and <150 characters counted as a shell →
  link density, sentence density and menu-block detection.
- **E-29** the extractor blacklisted tags but never selected main content →
  the main-content region is isolated (`.mw-parser-output`, `<main>`,
  `<article>`, `.post-content`, …), so a 111-language menu no longer eats the
  budget.
- **E-30** the browser's rendered-text collector leaked every inline
  `<script>`/`<style>` → those subtrees are skipped outright.
- **E-31** the DOM stub crashed a page script on `getAttribute` of a
  non-element → `Node` exposes a safe accessor.
- **E-32** `mode=auto` retried a known-dead renderer forever → a transport
  health memory, and the read says why it did not render.
- **E-33** the query string was invisible and there was no redirect chain →
  `effective_url` and `redirect_chain` on every artifact, query preserved.
- **E-34** the harnesses lived only inside the audit bundle → they are in the
  repository now (`benchmarks/acceptance_17.py`, `benchmarks/run_scraping.py`,
  `benchmarks/run_audit3000.py`) with a hash-verified 3,000-query corpus.
- **E-35** the documentation contradicted the code → `README.md`,
  `KNOWN_LIMITATIONS.md`, `BENCHMARKS.md` and `CHANGELOG.md` now match the
  measured behaviour.

---

## 3. The measured results

Everything below was produced by a command in this repository, in the
verification environment, on 2026-10-18.

| what | command | result |
|---|---|---|
| the test suite | `.venv/bin/python -m pytest -q` | **578 passed, 14 skipped, 0 failed** |
| lint | `.venv/bin/ruff check --select E9,F,B .` | clean |
| types | `.venv/bin/mypy iralens` | clean (81 source files) |
| the audit's 17 acceptance checks, inverted | `python benchmarks/acceptance_17.py` | **17/17** |
| the scraping test | `python benchmarks/run_scraping.py` | **12/12** |
| the frozen 3,000-query corpus through the engine | `python benchmarks/run_audit3000.py --offline-pipeline` | **12,186/12,186 clean** |
| the audit's own captured 3,000-row run, re-scored | `python benchmarks/run_audit3000.py --replay <baseline>` | 28,521 checks: **9,458 code-caused**, **4,935 external**, 14,128 clean |
| the live 3,000-query run | `python benchmarks/run_audit3000.py --live` | **not_run** — see §4 |

The 17 checks and the 12 scraping probes are the *inverse* of the audit's
baseline assertions: the audit's script asserted the defects were still
present (all 17 `true` on the baseline); these assert they are gone.

---

## 4. What could not be measured, and why

The verification environment reaches **package indexes and GitHub only**.
The search providers (DuckDuckGo, Bing, Wikipedia) and arbitrary web pages
are unreachable, so:

- **The live 3,000-query run is not measured.** The runner is in place and
  reports `not_run` with the reason instead of a number. On an unrestricted
  network it is one command.
- **The provider-side failures are not IraLens defects and were never
  fixable in code.** The audit's own root-cause text says so: the
  DuckDuckGo captcha is "provider-side", the S-AV root cause is "Unknown —
  further investigation required", and the 4,935 external failures in the
  baseline replay are captchas and throttles. The actionable residue of
  S-AV — the unconfigured `semantic-search` engine in the default chain — is
  E-23, and it is fixed.
- **The audit's S-RL family (45 relevance misses) is a retrieval-quality
  judgement, not a code defect.** The audit's own confidence field says the
  upstream cause is unresolved. What *was* a defect is the reporting: a
  nonempty set of filler was reported as `no_results_reason: "none"`. That
  is E-22, and it is fixed — the results are still what the provider
  returned, but the response now says the question was not addressed.

So a raw "3,000/3,000 zero failures" was never attainable by code alone,
and this report does not claim it. What *is* claimed, and measured, is:

- every **code-caused** failure the audit could identify is fixed, and each
  fix has a named regression test;
- the **3,000-query pipeline** is clean on every check the code controls
  (12,186/12,186);
- the **scraping test** is 12/12 and the **acceptance suite** is 17/17;
- what remains is named, classified and reproducible.

---

## 5. Honest notes on the audit itself

- Two of its assertions encoded the *defects* and had to be rewritten
  (`tests/test_wikipedia.py` asserted a revision timestamp as
  `published_at`; `tests/test_engine_classify.py` asserted an untyped error
  fallback). Both were rewritten to assert the fixed behaviour, and the
  change is visible in the Phase A commit.
- Its §8 cross-references for W-0029 and W-0030 point at the wrong entries;
  the correct ones are W-0024 (content guard) and W-0025 (state files).
- The audit's `README.md` states plainly that its benchmark is *incomplete*:
  3,050 of 6,000 evaluations, 50 of 3,000 fully paired, and no
  system-wide accuracy rate claimed. That is the correct description, and it
  is why the numbers above are split into "code-caused" and "external"
  rather than rolled into one score.

---

## 6. Where to look

| path | what it holds |
|---|---|
| `docs/audit/ERROR_REGISTER.md` | every defect, the cross-audit ID map, the de-duplications, the phase plan and the status |
| `docs/audit/FINAL_REPORT.md` | this document |
| `tests/test_audit_acceptance.py` | the 17 checks, inverted |
| `tests/test_audit_phase_a.py` | one test per E-01…E-13 |
| `tests/test_audit_phase_b.py` | one test per E-14…E-27 |
| `tests/test_audit_phase_c.py` | one test per E-28…E-35 |
| `benchmarks/acceptance_17.py` | the 17 checks as a standalone runner |
| `benchmarks/run_scraping.py` | the scraping-test runner |
| `benchmarks/run_audit3000.py` | the 3,000-query runner (live, replay, offline-pipeline) |
| `benchmarks/audit3000/` | the frozen, hash-verified 3,000-query corpus and the issue definitions |
| `tests/fixtures/audit/` | the audit's captured evidence, kept so the checks are reproducible |
