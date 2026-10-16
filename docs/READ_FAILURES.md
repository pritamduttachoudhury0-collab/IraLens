# 60-page audit: failure-cause triage and classification

Based on the audit results recorded in `results/2026-10-16/REPORT.md`, 27 of 60
page reads failed (33 passed, 55% overall). The failures are classified into the
five requested categories:

| Cause | Number of the 27 | Evidence & breakdown from audit | Remediation status |
|---|---:|---|---|
| **not_enough_text** | 11 | • 4 script-heavy JS app shells (client-rendered without DOM)<br>• 4 text PDFs returning sparse text or ligature breaks<br>• 3 scanned PDFs with no extractable text layer | **FIXED** (D-081 JS fallback, D-096 local OCR auto-attempt, D-097 text normalization & Poppler retry) |
| **other** | 6 | • 6 unrecoverable upstream errors (HTTP 404, DNS/connect drops) | **HANDLED** (Typed errors, honest no-guess reporting) |
| **blocked** | 5 | • 5 normal pages hitting HTTP 403 / 429 / bot challenges | **HANDLED** (Polite headers, browser fallback when available) |
| **too_slow** | 3 | • 3 requests hitting the read timeout/deadline on slow drips | **FIXED** (Bounded streaming deadline, D-097 20 MB cap) |
| **wrong_format** | 2 | • 2 PDFs with format mismatches (BOM prefix, `/pdf/` URL path) | **FIXED** (D-097 BOM-safe magic-byte sniffing & `/pdf/` path detection) |
| **Total** | **27** | **Full inventory of the 27 failed reads** | |

## Prioritized fixes

1. **Most common cause: `not_enough_text` (11/27)**
   - *Text PDFs:* Expanded pypdf extraction with NFKC normalization, ligature repair, and hyphen unwrapping (`_normalize_pdf_text`). Added local `pdftotext -layout` fallback when `pypdf` extracts sparse text (<80 chars).
   - *Scanned PDFs:* Auto-attempt local Poppler/Tesseract OCR when binaries exist, with clear typed `pdf_ocr_support_missing` when absent.
   - *Script-heavy pages:* Automatic engine fallback for JS shells in `auto` mode (D-081).

2. **Next cause: `wrong_format` and `too_slow` (5/27)**
   - BOM and leading whitespace support in `looks_like_pdf` within 1024 bytes.
   - Recognition of `/pdf/` URL paths (e.g. `arxiv.org/pdf/<id>`) in `read_direct`.
   - Separate 20 MB bounded limit for PDFs while maintaining wall-clock deadline.

3. **Remaining causes: `blocked` and `other` (11/27)**
   - Polite HTTP headers, proper User-Agent, typed classification of rate limits and bot challenges.
