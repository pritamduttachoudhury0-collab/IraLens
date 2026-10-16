# Page-read failure triage — 2026-10-14

Companion to the [paid-search replacement audit](paid-search-replacement-audit-2026-10-14.md).
Of the 60 audited page reads, 27 failed (33 passed, 55% overall). This note
classifies all 27 failures and records the remediation shipped for each
cause. The regenerated table comes from `scripts/triage_reads.py`.

| Cause | Number of the 27 | Breakdown from the audit | Remediation status |
|---|---:|---|---|
| **not_enough_text** | 11 | • 4 script-heavy JS app shells (client-rendered without DOM)<br>• 4 text PDFs returning sparse text or ligature breaks<br>• 3 scanned PDFs with no extractable text layer | **FIXED** — automatic browser fallback for JS shells, local OCR auto-attempt, PDF text normalization with a local `pdftotext` retry |
| **other** | 6 | • 6 permanent upstream errors (HTTP 404, DNS/connect drops) | **HANDLED** — typed errors, honest no-guess reporting |
| **blocked** | 5 | • 5 normal pages hitting HTTP 403 / 429 / bot challenges | **HANDLED** — polite headers, browser fallback when available |
| **too_slow** | 3 | • 3 requests hitting the read timeout/deadline on slow drips | **FIXED** — bounded streaming deadline, separate 20 MB PDF cap |
| **wrong_format** | 2 | • 2 PDFs with format mismatches (BOM prefix, `/pdf/` URL path) | **FIXED** — BOM-safe magic-byte sniffing and `/pdf/` path detection |
| **Total** | **27** | **Full inventory of the 27 failed reads** | |

## Prioritized fixes

1. **Most common cause: `not_enough_text` (11/27)**
   - *Text PDFs:* expanded `pypdf` extraction with NFKC normalization,
     ligature repair, and hyphen unwrapping (`_normalize_pdf_text`); local
     `pdftotext -layout` fallback when `pypdf` extracts sparse text
     (<80 chars).
   - *Scanned PDFs:* automatic local Poppler/Tesseract OCR when the binaries
     exist, with a clear typed `pdf_ocr_support_missing` when absent.
   - *Script-heavy pages:* automatic engine fallback for JS shells in
     `auto` mode.

2. **Next causes: `wrong_format` and `too_slow` (5/27)**
   - BOM and leading-whitespace support in `looks_like_pdf` within 1024 bytes.
   - Recognition of `/pdf/` URL paths (e.g. `arxiv.org/pdf/<id>`) in
     `read_direct`.
   - Separate 20 MB bounded limit for PDFs while keeping the wall-clock
     deadline.

3. **Remaining causes: `blocked` and `other` (11/27)**
   - Polite HTTP headers with a proper User-Agent, and typed classification
     of rate limits and bot challenges.

The remediation set is covered by regression tests
(`tests/test_pdf_autodetect.py`, `tests/test_pdf_text_quality.py`,
`tests/test_pdf_and_js_fallback.py`, `tests/test_read_triage.py`). The live
re-measurement against the full 60-URL set awaits an open-network benchmark
run; see `BENCHMARKS.md`.
