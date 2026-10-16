# IraLens paid-search replacement audit

**Audit date:** 2026-10-16 (Asia/Calcutta)  
**Repository/base:** `pritamduttachoudhury0-collab/IraLens`, `arena/10d16107-iralens`, product commit `b9a162763050d8c4cf2510b5914d431852612593`  
**Audit branch:** `audit/paid-search-replacement-20261014-phase1`  
**Network:** Google Cloud egress; no VPN intentionally used or detected.  
**Question:** Can IraLens replace a paid search API key?

## Test table

| Test | Status | Key numbers / result | Evidence |
|---|---|---|---|
| 1. Clean install, doctor, pytest including live tests, interface verifier | **PASS** for the completed run | Clean venv; doctor/browser healthy; **416 passed, 0 failed, 0 skipped**; verifier **22/22**. The broader three-runs/two-hours-apart requirement was **NOT RUN**. | [`../2026-10-14/phase-tests-1-2/SUMMARY.md`](../2026-10-14/phase-tests-1-2/SUMMARY.md), [`../2026-10-14/phase-tests-1-2/raw/pytest-full.txt`](../2026-10-14/phase-tests-1-2/raw/pytest-full.txt) |
| 2. Search sources alone | **PASS**, with SearXNG limits | DuckDuckGo **15/15** and Bing **15/15** worked independently. Bundled SearXNG instances: **0 / NOT RUN**. Self-hosted SearXNG: **BLOCKED**, `docker: command not found`. | [`../2026-10-14/phase-tests-1-2/SUMMARY.md`](../2026-10-14/phase-tests-1-2/SUMMARY.md) |
| 3. Frozen search-quality benchmark | **PASS** | 100/100 returned results; exact-site top 3 **24/25 (96%)**; facts top 10 **25/25 (100%)**; recent news **20/20 (100%)**; non-English 15/15; obscure 15/15. | [`phase-test-3-100/SUMMARY.md`](phase-test-3-100/SUMMARY.md), [`phase-test-3-100/raw/all_queries_results_verdicts.json`](phase-test-3-100/raw/all_queries_results_verdicts.json) |
| 4. Page reading | **FAIL** | Overall **33/60 (55%)**, required 90%; normal **20/25 (80%)**, required 97%; script-heavy **8/12 (66.7%)**, required 80%; text PDF **4/10 (40%)**, required 90%; scanned PDF **0/3**; dead links **2/2** correctly reported. | [`phase-tests-4-8/SUMMARY.md`](phase-tests-4-8/SUMMARY.md), [`phase-tests-4-8/raw/reading-verdicts.final.json`](phase-tests-4-8/raw/reading-verdicts.final.json) |
| 5. Research mode | **PASS**, with citation-quality caveat | **12/12** reports contained the correct answer; **0 invented citations**; 209 statements, 225 citation links; **223 fully supporting, 2 partially supporting** compound statements. | [`phase-tests-4-8/raw/research-verdicts.final.json`](phase-tests-4-8/raw/research-verdicts.final.json), [`phase-tests-4-8/raw/research-citation-review.txt`](phase-tests-4-8/raw/research-citation-review.txt) |
| 6. Load | **FAIL** | 226/226 completed; blocks **0%**; crashes **0**; median **1.440 s**; overall p95 **18.789 s**, required <6 s; 20-way burst median **23.655 s**; sustained-200 p95 **4.120 s**; health state survived restart. Recovery: **NOT APPLICABLE**, no block occurred. | [`phase-tests-4-8/raw/load/verdict.json`](phase-tests-4-8/raw/load/verdict.json), [`phase-tests-4-8/raw/load/records.jsonl`](phase-tests-4-8/raw/load/records.jsonl) |
| 7. HTTP API / paid-style plug-in | **PASS** for basic Serper-shaped web search | Missing/wrong key 401; bad input 400; >1 MiB body 413; repeated calls **50/50 HTTP 200**; response shape valid; tiny paid-style agent worked after changing only URL and key. | [`phase-tests-4-8/raw/api/results.json`](phase-tests-4-8/raw/api/results.json), [`phase-tests-4-8/harness/paid_style_agent.py`](phase-tests-4-8/harness/paid_style_agent.py) |
| 8. Safety | **PASS** for tested cases | Private/metadata/redirect attempts **15/15 blocked**; hidden-instruction samples **3/3 flagged**; bypasses **0**; secret leaks **0**. | [`phase-tests-4-8/raw/safety/results.json`](phase-tests-4-8/raw/safety/results.json) |
| 9. Packaging | **PASS** for wheel; Docker **NOT RUN** | Wheel built, installed into a fresh environment, `--version`, `doctor`, and live keyless search worked first try. Docker absent: exact error `/bin/bash: docker: command not found` (127). | [`finalization/packaging/raw.txt`](finalization/packaging/raw.txt), [`finalization/packaging/docker.txt`](finalization/packaging/docker.txt), [`finalization/packaging/wheel/`](finalization/packaging/wheel/) |
| 10. Paid comparison | **NOT RUN** | No paid or free-tier search-provider key was supplied; top-5 overlap cannot be measured. | This report |
| 11. Seven-day watch | **NOT RUN** | Free GitHub Actions daily workflow and runner created for frozen **30 queries + 20 URLs**, with 90-day artifacts. Zero daily runs exist; seven-day result cannot be claimed. Workflow will not execute until the branch is pushed/merged. | [`../../.github/workflows/daily-watch.yml`](../../.github/workflows/daily-watch.yml), [`../../scripts/daily_watch.py`](../../scripts/daily_watch.py), [`finalization/daily-watch/verification.txt`](finalization/daily-watch/verification.txt) |
| 12. Outside-site rules/terms inventory | **COMPLETED — HIGH-RISK FLAGS** | Default keyless search parses DuckDuckGo and Bing consumer HTML, with no clearly authorized search-API contract. Official APIs are conditional; arbitrary page/RSS reading is site-specific; several logged-in social/browser integrations look restricted without permission. | [`finalization/OUTSIDE-SITES.md`](finalization/OUTSIDE-SITES.md), [`../../docs/SOURCE_POLICY.md`](../../docs/SOURCE_POLICY.md) |

## Verdict: NOT READY

In plain language: **IraLens cannot currently replace a paid search API as a general production service.** Its keyless search is surprisingly capable at low sustained volume: two independent engines worked, the frozen quality benchmark passed, the basic Serper-shaped API swap worked, block rate was zero, and the tested safety controls held. The wheel also installed and searched successfully in a fresh environment.

But replacement requires more than good result relevance. Page reading failed badly—only 55% overall, with especially poor PDF, scanned-PDF, and non-English performance. Concurrent traffic queues behind the two-search gate: the 20-request burst had a 23.655-second median and drove overall p95 to 18.789 seconds. Default discovery also relies on automated HTML access to DuckDuckGo and Bing rather than a licensed search API, creating a material terms/continuity risk. Seven-day stability, three spaced full-suite runs, Docker deployment, self-hosted SearXNG, and paid-provider parity remain unproven.

IraLens is suitable today for **experimentation and low-volume, operator-controlled keyless search**, not as an unrestricted drop-in paid-search replacement.

## Top 10 failures and blockers

1. **Page reading misses the overall bar by 35 points (55% vs 90%).** Likely cause: direct-fetch failures, inconsistent browser fallback, inaccessible targets, and weak format/language coverage.
2. **Concurrent-search p95 is 18.789 s vs the 6 s limit.** Likely cause: the shared concurrency gate allows only two active searches, so 5- and 20-way bursts queue for tens of seconds.
3. **Text-PDF reading is 40% vs the 90% requirement.** Likely cause: URL/server incompatibilities and incomplete PDF fetch/extraction coverage; several frozen PDF URLs returned typed failures or too little text.
4. **Scanned-PDF reading is 0/3.** Likely cause: OCR is disabled by default and required local OCR tools were unavailable.
5. **Normal-page reading is 80% vs the 97% requirement.** Likely cause: direct network/site denial failures and fallback not recovering enough ordinary pages.
6. **Default keyless search has high terms/continuity risk.** Likely cause: DuckDuckGo and Bing backends parse consumer HTML pages rather than use an expressly licensed search API.
7. **No seven-day stability evidence exists.** Likely cause: the scheduled workflow is newly created and cannot run until pushed/merged; seven completed daily artifacts are required.
8. **The required three full-suite runs at least two hours apart were not completed.** Likely cause: the audit was executed in short requested phases, so repeatability over time remains unknown.
9. **No paid-provider parity result exists.** Likely cause: no paid or free-tier provider key was supplied, so top-5 overlap is unmeasured.
10. **Self-hosted SearXNG and Docker deployment are unverified.** Likely cause: Docker is absent in the audit sandbox; there are also zero bundled public SearXNG instances by design.

## Additional limits

- The compatibility endpoint covers basic Serper-shaped web search, not paid-provider news, images, maps, shopping, or provider-specific fields.
- Research reports answered all 12 questions but emitted many irrelevant single-source statements; two citation links only partially supported compound wording.
- The 100-query quality run is one cloud-network observation, not a guarantee across regions, IP reputation, or time.
- Technical success does not establish permission to automate or redistribute third-party results/content.

## Delivery status

Results are committed locally on `audit/paid-search-replacement-20261014-phase1`. GitHub push remains **BLOCKED** because no GitHub credential is available in the sandbox. The exact prior error was:

```text
fatal: could not read Username for 'https://github.com': No such device or address
```
