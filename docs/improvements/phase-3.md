# Phase 3 — Non-Latin and multilingual retrieval (D3)

## Changes

- `querytext.tokenize()` normalizes Unicode to NFC; spaceless Thai, Lao, Khmer and
  Myanmar scripts receive overlapping bigrams, in addition to the existing CJK
  handling. Devanagari, Bengali, Arabic, Cyrillic, Greek, Hebrew, Armenian, Georgian
  and Ethiopic retain Unicode tokens instead of collapsing to empty sets.
- New `language.detect_language()` identifies scripts and maps common scripts to
  Wikipedia editions. Yoruba Latin diacritics receive a best-effort `yo` hint.
- Wikipedia queries a detected-language edition first, falls back to English when
  the local edition returns no hits, and marks the edition's URL correctly. User
  language values are reduced to a validated primary-subtag host label.
- Bing now sends a detected language hint for non-English query scripts.

## Checks and limits

- Regression test includes Yoruba, Hindi, Thai, Bengali, decomposed-vs-composed
  Unicode, and Wikipedia edition URL checks.
- `evaluate_offline.py` found 0/300 empty query term sets; 10 holdout queries use
  non-Latin scripts (Amharic, Armenian, Bengali, Cyrillic, Georgian, Khmer, Myanmar).
  This is tokenization/script coverage, **not retrieval relevance**.
- No language identification model was added. Latin-script Hausa and most Latin
  languages cannot be reliably identified without a language-ID implementation;
  Yoruba recognition is heuristic. Zero unrelated-language results, Cat-9 proxy
  gain, and ≥3 results per non-Latin query are **NOT MEASURED**.
