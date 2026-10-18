# IraLens Error Register

**Scope.** Every error identified across the five supplied audit documents, plus every
defect those documents reveal in the IraLens codebase. Canonical IDs `E-nn` address
code defects; `D-nn` address errors *in the audit documents themselves*.

**Audited baseline.** Commit `555dca76cba363278ae309599fb29326d9d0205b` (`v0.2.1`).
Verified: the audit bundle's `iralens-baseline-source.tar.gz` (SHA-256
`aa8b590dd52671e18e41d6d8968172ca111cb72e74ab44a963948bbf7c8da935`) is
**byte-for-byte identical** to the repository tree at that commit
(`diff -rq --exclude=.git` → only `__pycache__` differs). Every code location
cited by the 3,000-query audit is therefore directly actionable.

**Baseline test state.** 463 passed, 14 skipped (offline), reproduced 2026-10-18.

## Source documents

| Ref | File | Size | Content |
|-----|------|------|---------|
| **A1** | `weakness.md` | 1,610 lines | Reddit + Indie Hackers scraping stress test. 30 failures (`W-0001`…`W-0030`), 7 systemic groups (`S1`…`S7`), 24-item fix queue, acceptance criteria `A1`–`A8`, uncertainties `U1`–`U7`. |
| **A2** | `weaknesses_part1.md` + `weaknesses_part2.md` | 153,143 lines | 3,000-query benchmark report (readable edition). §1–§13, 7,553 failure rows, 14 systemic groups (`S-AV`…`S-BLOCK`), 13-item fix queue, 99 embedded evidence files. |
| **A3** | `weaknesses_part3.md` + `weaknesses_part4.md` | 38,958 lines | Evidence layer: raw `evaluations.jsonl` / `failures.jsonl` / `iralens.jsonl`, the byte-exact baseline source archive, the 128,635-line byte-preserved original report, consolidation statement. |

### Cross-audit ID map

All 30 `W-nnnn` IDs and all 14 `S-*` IDs are accounted for below. Nothing is
dropped; where two audits describe one defect the entry lists both.

| Audit ID(s) | Canonical | Phase |
|---|---|---|
| W-0001, S-SITE, S1 | E-15, E-16 | B |
| W-0002, S1 | E-15, E-16, E-20 | B |
| W-0003, S1 | E-23 | B |
| W-0004, S4, S7 | E-17, E-18 | B |
| W-0005, S2, S7 | E-20 | B |
| W-0006 | E-21 | B |
| W-0007, W-0023, S4 | E-27 | B |
| **W-0008, S3** | **E-01** | **A** |
| W-0009, S5 | E-06 | A |
| W-0010, S2 | E-19 | B |
| W-0011, P3-4 | E-24 (+ U2) | B |
| W-0012, S2 | E-14, E-22 | A / B |
| W-0013, S5 | E-05 | A |
| W-0014, S5 | E-33, E-34 | C |
| W-0015 | *(not a defect)* | — |
| W-0016 | *(not a defect)* | — |
| W-0017, W-0018, S4 | E-17 | B |
| W-0019, S2 | E-28 | B |
| W-0020, S7 | E-26 | B |
| W-0021, S7 | E-26 | B |
| W-0022 | E-21 | B |
| W-0024, S6 | E-12 | A |
| W-0025, S6 | E-11 | A |
| W-0026, S6 | E-25 | B |
| W-0027, S4 | E-22 | B |
| W-0028, S3 | E-29 | C |
| W-0029, U4 | E-35 | C |
| W-0030, S5 | E-07 | A |
| S-AV (3,000 rows) | E-24 (+ external) | B |
| S-DT (2,270 rows) | E-04 | A |
| S-SN (2,222 rows) | E-03 | A |
| S-RL (45 rows) | E-23 | B |
| S-CACHE | E-02 | A |
| S-CAP | E-09 | A |
| S-RATE | E-06, E-14, E-22 | A / B |
| S-SESSION | E-10 | A |
| S-BLOCK | E-08 | A |
| S-TEXT | E-33 | C |
| S-MAIN | E-32 | C |
| S-JS | E-31 | C |
| S-ACCESS | *(not a defect)* | — |

---

# Part 1 — Code defects (the work)

## Phase A — Integrity & Truthfulness

*The system must stop reporting things that are not true.*

| ID | Title | Location | Audit refs |
|----|-------|----------|------------|
| **E-01** | Reddit browser read **discards `navigate()` status**; a 403 bot-wall page is returned as a *successful post*. Contrast `web.py:451-452`, which does check status. | `iralens/sources/reddit.py:99-114` | W-0008, S3, P0-1 |
| **E-02** | **Cached page inherits the previous page's transport metadata.** The cache stores only `{markdown, method}`; on a hit, `_static_artifact` reads the stale thread-local `_DIRECT_META_LOCAL.last` and reports the *wrong* `final_url`/`status_code`/`content_type`/`redirected`. A cached React read reports itself as the Brahmaputra River page. | `iralens/sources/web.py:406-427`, `iralens/sources/web.py:578-614` | S-CACHE, U1, S3 |
| **E-03** | Wikipedia requests `srprop=timestamp` **only**; the API omits `snippet` and the parser substitutes `""` → every Wikipedia hit has an empty snippet (2,222 rows). | `iralens/search/engines/wikipedia.py:46-47, 108` | S-SN |
| **E-04** | Wikipedia **revision** timestamp is emitted as `published_at`, then marked `date_status=known` and used for freshness (2,270 rows). A page edited today looks newly published. | `iralens/search/engines/wikipedia.py:50, 57`; `iralens/search/engine.py:314-345` | S-DT |
| **E-05** | `IraLensError.to_dict()` **drops `detail`**, so no structured output ever carries the developer-facing cause. Every retry decision is blind. | `iralens/errors.py:26-30` | W-0013, S5, P1-5 |
| **E-06** | HTTP status classification is **mode-unaware and misattributes the origin**: 401/403 always hint "retry with mode=browser" (even when already in browser mode); a 429 from the *origin* is reported as "the reader service is rate-limited". | `iralens/sources/web.py:296-333` | W-0009, S-RATE, S5 |
| **E-07** | Browser-engine JS failures escape the taxonomy as a generic `error` envelope with a raw V8 stack as the message. | `iralens/_internal/browser_engine/runtime.py:169, 235, 236` | W-0030, S5, P2-5 |
| **E-08** | `classify_block` scans the body for **bare substrings** (`"429"`, `"rate limit"`, `"challenge"`), so benign prose ("Reference item 1429") classifies as `rate_limited` and a *valid* one-hit SERP triggers a needless browser navigation. | `iralens/reliability.py:35-53`; `iralens/search/engines/bing.py:115-123` | S-BLOCK, S3 |
| **E-09** | `truncate_text` appends the truncation notice **outside** the budget, so `len(content) > max_chars` by 24–26 chars. | `iralens/content_guard.py:69-79` | S-CAP |
| **E-10** | `Session.save` writes to a **fixed** `session.tmp` name then `os.replace` → concurrent writers race and lose updates (`FileNotFoundError`). Same pattern for `browser_state.json`. | `iralens/session.py:55-57, 114-116` | S-SESSION |
| **E-11** | State files (`session.json`, `browser_state.json`) are written **0644** — world-readable cookies and full history. | `iralens/session.py:56, 115` | W-0025, S6, P2-9 |
| **E-12** | **Content guard is per-path, not central.** `content_guard.scan()` runs only in `web.py`; `RedditSource.read_url` and `RSSSource.read_url` never scan, so their artifacts carry **no** `prompt_injection:*` flags. | `iralens/sources/reddit.py`, `iralens/sources/rss.py`, `iralens/core.py` | W-0024, S6, P0-2 |
| **E-13** | RSS status is discarded: `raise_for_status()` → `PageUnavailableError("could not download the feed")`, so a 429 is indistinguishable from a 404 and `Retry-After` is lost. | `iralens/sources/rss.py:57-61` | W-0012, S-RATE |

## Phase B — Completeness & Scale

*The system must find, scope and return everything it is asked for.*

| ID | Title | Location | Audit refs |
|----|-------|----------|------------|
| **E-14** | `site:` operator **destroyed by the tokenizer** (`:` and `/` are not run chars) and by the reformulation keyword variant → `site:reddit.com/r/SaaS` becomes `"site reddit.com saas"`. | `iralens/search/querytext.py:32`; `iralens/search/reformulate.py:34-56` | W-0001, S1 |
| **E-15** | `site:` scope is **not enforced** in post-filtering; only structured `SearchFilters` are applied, so out-of-scope results are returned for a scoped query. | `iralens/search/engine.py:266-285` | S-SITE, W-0001 |
| **E-16** | **No pagination anywhere** — no `offset`/`page` on search, no `after` cursor on Reddit, no `page` on hubs. Repeat queries are byte-identical, so the corpus cannot grow past page 1. | `iralens/search/schema.py:90-101`; `iralens/search/engine.py` | W-0004, W-0017, W-0018, S4, P1-2 |
| **E-17** | **No subreddit / community post-listing operation** — Reddit supports only "read this URL". | `iralens/sources/reddit.py` | W-0004, S7, P1-6 |
| **E-18** | `RedditSource` **never attempts the working unauthenticated RSS backend** (`/r/<sub>/.rss`); `backends = ["browser-bridge","rdt-cli","browser"]`. The only transport that works anonymously is unwired. | `iralens/sources/reddit.py:34` | W-0010, S2, P1-1 |
| **E-19** | **No IndieHackers source module** — the platform is unreachable except through generic page reads. | `iralens/sources/__init__.py` | W-0005, W-0002, S2, S7, P2-1 |
| **E-20** | Date filters **keep undated results**, so a recency window cannot be enforced; `published_at` is unknown for 72% of results. | `iralens/search/engine.py`; `iralens/search/schema.py` | W-0006, W-0022, P2-3 |
| **E-21** | **No shared per-host rate budget** — `PerSiteConcurrencyGate` bounds concurrency but never *paces* requests, and `Retry-After` is never honoured. Bursts self-inflict 429s and captchas. | `iralens/reliability.py:398-431`; `iralens/sources/web.py` | W-0027, W-0012, S4, P1-4 |
| **E-22** | **No answer-coverage gate.** A nonempty result set of generic filler is reported as `no_results_reason: "none"`; research coverage can reach "success" on irrelevant hits. | `iralens/search/engine.py:197-205`; `iralens/search/ranking.py:75-107` | S-RL (45), P1 |
| **E-23** | The optional, unconfigured `semantic-search` (Exa, needs `mcporter`) is in the **default engine chain**, so the default chain is degraded by default and "not configured" is conflated with "runtime failure". | `iralens/settings.py:33`; `iralens/search/engines/exa.py:51-59` | S-AV (3,000), §7, P3-4 |
| **E-24** | `discovered_from` provenance is taken from the **stale session `last_query`**, misattributing how an artifact was found. | `iralens/core.py:211-214` | W-0026, S6, P2-4 |
| **E-25** | Post metadata is **prose-only, never typed** — author, score, comment count, created-at and permalink are not first-class fields on any post. | `iralens/model.py`; `iralens/sources/reddit.py` | W-0021, W-0020, S7, P2-2 |
| **E-26** | Dedup is **per-response only**; across a sweep the same URLs reappear (41.9% duplication) and waste the result budget. | `iralens/search/engine.py`; `iralens/search/dedup.py` | W-0007, W-0023, S4, P3-1 |
| **E-27** | RSS permalinks are lossy and non-canonical (no `urljoin`, no GUID synthesis); individual entry failures are invisible. | `iralens/sources/rss.py` | W-0019, S2, P2-8 |

## Phase C — Extraction, Resilience, Safety & Test-Readiness

*The system must extract correctly, survive hostile pages, and be verifiable.*

| ID | Title | Location | Audit refs |
|----|-------|----------|------------|
| **E-28** | `looks_like_js_shell` only catches explicit JS markers or <150 visible chars, so a 2,119-char application shell is accepted as a complete page. | `iralens/sources/web.py:90-103` | S-JS |
| **E-29** | The extractor blacklists tags but never **selects main content** (no `.mw-parser-output` / `<main>` / `article` preference), so a 111-language navigation list consumes the whole character budget. | `iralens/extract.py:39-45` | S-MAIN |
| **E-30** | Browser `innerText` **leaks inline `<script>`/`<style>` text** into extracted content; all 15 `<script>` elements compute as `display:block; visibility:visible`. | vendored Obscura `bootstrap.js` → `_collectInnerText` | S-TEXT, W-0014 |
| **E-31** | The Obscura DOM stub lacks `getAttribute` → V8 crash `s.getAttribute is not a function` on Indie Hackers pages, with no static-text fallback. | vendored Obscura `bootstrap.js` | W-0014, S5, P2-6 |
| **E-32** | `mode=auto` has **no transport health memory**; it routes into a known-dead browser path repeatedly instead of converging on the working one. | `iralens/sources/web.py` | W-0028, S3, P2-7 |
| **E-33** | Query parameters (`?page=`, …) are normalized away on reads and there is **no `effective_url` / redirect chain**, so deep-link fidelity cannot be observed. | `iralens/security.py`; `iralens/model.py` | W-0029, U4, P3-2 |
| **E-34** | **Test/benchmark readiness.** The frozen acceptance benchmark has zero rows; there is no 3,000-query runner, no scraping-test runner, and the audit's 17-check offline acceptance script lives only inside the audit bundle. | `benchmarks/`, `benchmark/`, `scripts/` | A2 §11, A3, P3 harness |
| **E-35** | **Documentation drift** — `KNOWN_LIMITATIONS.md`, `BENCHMARKS.md`, `CHANGELOG.md` and `README.md` do not reflect the post-fix behaviour, so the project cannot honestly claim its verification status. | `docs/`, `README.md` | A1 A8, A2 §11 |

---

# Part 2 — Errors in the audit documents themselves

These do not require code changes but are reported for completeness and because
they determine how much of each document can be trusted.

| ID | Error | Impact |
|----|-------|--------|
| **D-01** | **A1's code snippets are from a different codebase.** Cited files `search/query.py`, `search/normalize.py`, `state.py`, `js_shell.py`, `sources/indiehackers.py`, `rate.py` do not exist here. Its `ErrorType` enum, `content_guard.ScanResult`/`MAX_CHARS`, `_keywords_variant`/`t.kind`, `_RUN_CHARS`, `_feed_from_markdown`, `_source_for_url` also do not exist. | A1's *behaviours* remain useful; its *locations* must be re-derived. |
| **D-02** | **A1's harness could not have run verbatim.** R6 imports `from iralens import search_api, SearchOptions, open, fetch`; `iralens/__init__.py` exports only `Artifact`, `IraLens`, `SearchResult` and the error classes → `ImportError`. | A1's numbers came from a different codebase or a different import path. |
| **D-03** | A1 §8 "P1" cites **W-0029** for the content-guard bypass; the content-guard failure is **W-0024** (W-0029 is the `?page=` fidelity issue). | Mis-citation; corrected in the map above. |
| **D-04** | A1 §8 "P2" cites **W-0030** for world-readable state files; the state-file failure is **W-0025** (W-0030 is the untyped JS-error envelope). | Mis-citation; corrected in the map above. |
| **D-05** | A1 **W-0002** ("Indie Hackers undiscoverable") and **W-0003** ("community-discovery relevance too poor") are not independent defects — they are downstream symptoms of E-14/E-15 (no `site:` scope) and E-19 (no IH source). | Over-counts the defect total; consolidated into E-14/E-15/E-19. |
| **D-06** | A1 **W-0011** attributes a DuckDuckGo captcha to IraLens. The captcha is provider-side; the actionable residue is E-23 (default chain) plus untested breaker half-open recovery (**U2**). | Recategorized: not an IraLens defect. |
| **D-07** | A1's §9 "Source lines" fields point at a tree that does not match this repository in several records. | Locations unreliable; use A2's locations. |
| **D-08** | **A2's embedded-file blocks for `records/evaluations.jsonl` and `records/failures.jsonl` have no closing fence in their own part** — the block runs to the end of the file and continues in the next part. A naive extractor silently loses 2 of 101 files. | Observed directly: a standard extract returned 97/99; both files were recovered by scanning JSONL lines across part boundaries. |
| **D-09** | A3's "Consolidation verification" claims **101 original ZIP entries** recovered byte-for-byte. The ZIP itself is not in the bundle, so that specific claim is not independently verifiable. What *is* verifiable: 101 unique embedded paths exist; the tar.gz and the original `weaknesses.md` match their declared SHA-256 exactly; §5–§13 differ between the readable edition and the original by exactly 2 link rewrites. | Claim scope narrowed to what is checkable. |
| **D-10** | A2's readable edition rewrites relative evidence links to in-document anchors, so the original report's file tree is not directly navigable from the Markdown. | Cosmetic; anchors resolve. |
| **D-11** | A2's per-query evaluation records mark **0** of 3,000 queries `qualitative_evaluation_complete`, and `arena_executed` is true for only 50. A1-style "full 20-dimension evaluation" claims would be false. | The audits are self-consistent here; recorded so the benchmark is not misrepresented as a 6,000-evaluation pass. |

---

# Part 3 — Explicitly not defects

| Audit ID | Item | Why it is not a code defect |
|----------|------|---------------------------|
| W-0015 | No logged-in Reddit backend available | Environmental. The audit itself lists it under "no fix (external/accepted)". |
| W-0016 | Indie Hackers `/groups` → 410, topics → 404 | External site change. The IH adapter must avoid `/groups`; recorded as a design constraint. |
| S-ACCESS (3 rows) | NSE 403, example.com 404 | Correct behaviour — the system surfaced the failure honestly. |
| S-AV (external part) | DuckDuckGo captcha | Provider-side trigger; the audit states the trigger is unknown. Not bypassed. |
| S-RL (upstream part) | 45 relevance misses | The audit's own root cause is "Unknown — further investigation required". The *established* handling weakness is E-22. |

---

# Part 4 — Phase plan

### Status

| Phase | Defects | Status | Commit |
|---|---|---|---|
| A — Integrity & Truthfulness | E-01 … E-13 | **complete** | `ba8a658` |
| B — Completeness & Scale | E-14 … E-27 | **complete** | `14ad8be` |
| C — Extraction, Resilience, Safety & Test-Readiness | E-28 … E-35 | **complete** | `f7f1059` |

**Every entry in this register is implemented and regression-tested.** One
test per defect lives in `tests/test_audit_phase_a.py` (E-01…E-13),
`tests/test_audit_phase_b.py` (E-14…E-27) and `tests/test_audit_phase_c.py`
(E-28…E-35). The audit's own 17 offline acceptance checks are inverted into
`tests/test_audit_acceptance.py` and are also runnable on their own with
`python benchmarks/acceptance_17.py`. The consolidated verdict, the measured
numbers and the honest account of what could not be measured are in
[`FINAL_REPORT.md`](FINAL_REPORT.md).

**Phase A — Integrity & Truthfulness** (E-01 … E-13)
Stop the system from reporting false things: false success, false provenance, false
dates, false metadata, blind errors, false block detection, over-budget content,
racy state, loose permissions, unscanned content, invisible HTTP status.

*Exit criteria:* every artifact's URL, date, status and provenance is provably the
one that produced it; every error carries a typed, actionable, mode-aware cause;
every artifact is scanned; state files are `0600`; the character budget is exact.

**Phase B — Completeness & Scale** (E-14 … E-27)
Make the system find and return everything: `site:` scope, pagination, Reddit
listing + RSS fallback, Indie Hackers source, date filtering, per-host rate pacing,
answer-coverage gating, a healthy default engine chain, correct provenance, typed
post metadata, cross-query dedup, canonical RSS permalinks.

*Exit criteria:* scoped queries return only in-scope results; pagination returns
distinct pages; Reddit and Indie Hackers are first-class; the default chain
degrades gracefully and says so; results carry a coverage verdict.

**Phase C — Extraction, Resilience, Safety & Test-Readiness** (E-28 … E-35)
Extract the right text, survive hostile pages, and make the whole thing verifiable:
JS-shell detection, main-content selection, script leakage, DOM-stub hardening,
auto-mode transport memory, deep-link fidelity, and — critically — the two scoring
harnesses plus the inverted 17-check acceptance suite and honest documentation.

*Exit criteria:* extraction returns main content, not navigation; no script text in
output; the 3,000-query runner and the scraping-test runner execute end-to-end and
report a score; documentation matches measured behaviour.

**Ordering rationale.** A is first because a defect that *lies* corrupts
everything downstream — a cache that misreports its URL poisons every later phase's
evidence. B is second because scope and scale cannot be measured while A is still
falsifying provenance. C is last because the harnesses in C must measure the
*fixed* system, and because extraction work is only meaningful once the content
it extracts is honestly attributed.
