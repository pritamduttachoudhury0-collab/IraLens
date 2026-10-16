# 60-page audit: failure-cause table (pending source data)

**Not classified.** The requested `results/<date>/REPORT.md`, its 60-page read
records, and `audit/paid-search-replacement-20261014-phase1` are absent from
this checkout and from the GitHub remote as of 2026-10-16. The existing
`benchmarks/urls.json` is a *different*, frozen 100-URL corpus; its synthetic
self-test is not a replacement for the missing 60-page audit. The reported
**27 failed reads** are a user-supplied total, not locally verified evidence.
Do not rank causes or claim fixes to those 27 without their per-URL records.

| Cause requested | Number of the 27 | Evidence needed to classify |
|---|---:|---|
| Blocked | unknown | 401/403/429, auth/captcha/robots or security-policy error |
| Wrong format | unknown | MIME / PDF parser / unsupported-media details |
| Too slow | unknown | timeout/deadline with elapsed time |
| Not enough text | unknown | returned length, missing phrase, OCR/render status |
| Other | unknown | URL, error, text length and expected phrase for manual review |
| **Total** | **27 reported, 0 individually available** | **audit branch/report/results required** |

Once the audit report and per-URL JSON have been supplied, read the report
*first*, then run `python scripts/triage_reads.py path/to/results.json --out
path/to/triage.md` for the benchmark `extraction.urls` schema. Review every
classification with the raw audit record: a typed refusal on a walled page is
not automatically a benchmark failure. Do not edit the source audit file or
frozen tests. Fix the most frequent **verified, actionable** cause first.
