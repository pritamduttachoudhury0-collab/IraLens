# Phase 4 — Provenance, freshness and extraction (D7)

## Changes

- Added a bounded, offline `extract_date(snippet, url)` for ISO dates, unambiguous
  written dates, and `/YYYY/MM/DD/` or `/YYYY-MM-DD` paths. It returns both ISO date
  and evidence source; ambiguous numeric dates are not guessed. No result pages are
  fetched to get dates.
- A result lacking an engine date is enriched from its snippet/URL before ranking;
  `RankedResult.date_evidence` exposes the pattern used.
- Added a 100-row extraction fixture and evaluator for expected-span presence.

## Results

- `.venv/bin/python benchmark/holdout/evaluate_extraction.py` → **100/100 (1.000)**
  expected spans present using the local HTML extractor on synthetic fixture pages.
  This is a deterministic smoke measure on generated HTML, not 100 independent
  live pages. The requested real-page extraction benchmark and exact-span accuracy
  on representative sites are **NOT MEASURED**.
- Snippet date parsing and provenance are regression-tested. Overall date coverage
  on search results is **NOT MEASURED** (benchmark raw results absent; live engines
  unavailable), so the ≥80% acceptance target is not claimed.
