# -*- coding: utf-8 -*-
"""Script/language detection for search queries (defect D3).

Deterministic, offline, script-first: the Unicode block of the dominant
non-Latin script decides the candidate language; Latin text with Yoruba
diacritics (ẹ/ọ/ṣ + tone marks) is recognised as Yoruba. This is used to
(a) pick a Wikipedia language edition, (b) pass a language hint to engines
that accept one, and (c) never silently tokenise a query to an empty set.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageInfo:
    script: str          # e.g. "Devanagari", "Latin", "Arabic"
    lang: str            # best-guess BCP-47 primary subtag
    wikipedia_lang: str  # Wikipedia edition to try first
    is_latin: bool = True


_RANGES = (
    ("Devanagari", "\u0900", "\u097f"), ("Bengali", "\u0980", "\u09ff"),
    ("Gurmukhi", "\u0a00", "\u0a7f"), ("Gujarati", "\u0a80", "\u0aff"),
    ("Oriya", "\u0b00", "\u0b7f"), ("Tamil", "\u0b80", "\u0bff"),
    ("Telugu", "\u0c00", "\u0c7f"), ("Kannada", "\u0c80", "\u0cff"),
    ("Malayalam", "\u0d00", "\u0d7f"), ("Thai", "\u0e00", "\u0e7f"),
    ("Lao", "\u0e80", "\u0eff"), ("Myanmar", "\u1000", "\u109f"),
    ("Georgian", "\u10a0", "\u10ff"), ("Hangul", "\uac00", "\ud7af"),
    ("Ethiopic", "\u1200", "\u137f"), ("Khmer", "\u1780", "\u17ff"),
    ("Sinhala", "\u0d80", "\u0dff"), ("Hebrew", "\u0590", "\u05ff"),
    ("Arabic", "\u0600", "\u06ff"), ("Armenian", "\u0530", "\u058f"),
    ("Greek", "\u0370", "\u03ff"), ("Cyrillic", "\u0400", "\u04ff"),
    ("Hiragana", "\u3040", "\u309f"), ("Katakana", "\u30a0", "\u30ff"),
    ("Han", "\u4e00", "\u9fff"),
)
_SCRIPT_LANG = {
    "Devanagari": "hi", "Bengali": "bn", "Gurmukhi": "pa", "Gujarati": "gu",
    "Oriya": "or", "Tamil": "ta", "Telugu": "te", "Kannada": "kn",
    "Malayalam": "ml", "Thai": "th", "Lao": "lo", "Myanmar": "my",
    "Georgian": "ka", "Hangul": "ko", "Ethiopic": "am", "Khmer": "km",
    "Sinhala": "si", "Hebrew": "he", "Arabic": "ar", "Armenian": "hy",
    "Greek": "el", "Cyrillic": "ru", "Hiragana": "ja", "Katakana": "ja", "Han": "zh",
}
_YORUBA_CHARS = set("ẹọṣẸỌṢ")


def detect_language(text: str) -> LanguageInfo:
    text = unicodedata.normalize("NFC", text or "")
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        if ch.isspace():
            continue
        if "a" <= ch.lower() <= "z" or ch.isascii():
            latin += 1
            continue
        for name, lo, hi in _RANGES:
            if lo <= ch <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
        else:
            latin += 1  # Latin Extended etc.
    if counts:
        script = max(counts, key=lambda k: counts[k])
        # Bengali block with Assamese-specific letters ৰ/ৱ is Assamese.
        lang = _SCRIPT_LANG.get(script, "en")
        if script == "Bengali" and any(c in text for c in "ৰৱ"):
            lang = "as"
        if script in ("Hiragana", "Katakana"):
            lang = "ja"
        return LanguageInfo(script=script, lang=lang, wikipedia_lang=lang, is_latin=False)
    if any(c in _YORUBA_CHARS for c in text) or text.count("́") + text.count("̀") >= 2:
        return LanguageInfo(script="Latin", lang="yo", wikipedia_lang="yo", is_latin=True)
    return LanguageInfo(script="Latin", lang="en", wikipedia_lang="en", is_latin=True)
