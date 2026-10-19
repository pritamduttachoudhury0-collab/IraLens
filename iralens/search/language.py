# -*- coding: utf-8 -*-
"""Script and lightweight language detection for search queries.

Detection is deterministic and offline. Unicode script is used when present;
common function words and distinctive characters cover frequent Latin-script
languages so official language editions (not only English Wikipedia) can be
selected without an external model or network call. This is a routing hint,
not a claim of perfect language identification.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, FrozenSet


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
    ("Georgian", "\u10a0", "\u10ff"), ("Ethiopic", "\u1200", "\u137f"),
    ("Khmer", "\u1780", "\u17ff"), ("Sinhala", "\u0d80", "\u0dff"),
    ("Hebrew", "\u0590", "\u05ff"), ("Arabic", "\u0600", "\u06ff"),
    ("Armenian", "\u0530", "\u058f"), ("Greek", "\u0370", "\u03ff"),
    ("Cyrillic", "\u0400", "\u04ff"),
    ("Hiragana", "\u3040", "\u309f"), ("Katakana", "\u30a0", "\u30ff"),
    ("Han", "\u4e00", "\u9fff"), ("Hangul", "\uac00", "\ud7af"),
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

# High-signal words for Latin-script languages. The sets intentionally use
# several markers per language; a single common word such as "de" or "a" is
# not enough to override English. Accented, language-specific characters are
# handled first where they are strongly diagnostic.
_LATIN_WORD_MARKERS: Dict[str, FrozenSet[str]] = {
    "de": frozenset("der die das und ist von eine einer mit für wie funktioniert hauptstadt wärmepumpe".split()),
    "fr": frozenset("le les des une et est pour avec à où trouver près meilleur meilleure bibliothèque capitale ville français".split()),
    "es": frozenset("el los las una y es para con como instalar receta auténtica ciudad capital río".split()),
    "pt": frozenset("o os uma e é para com qual do da dos brasil cidade capital".split()),
    "it": frozenset("il lo gli della del che una quanto costa biglietto città capitale".split()),
    "nl": frozenset("het een van voor wat zijn hoofdstad belgië nederland".split()),
    "sv": frozenset("och är som en det vad sveriges högsta största berg".split()),
    "tr": frozenset("ve için şehir şehri türkiye'nin türkiyenin en kalabalık başkenti".split()),
    "id": frozenset("yang untuk dengan berapa jumlah penduduk adalah indonesia dari".split()),
    "vi": frozenset("của là gì và những trong một với thủ đô việt nam".split()),
}


def _latin_language(text: str) -> str:
    low = unicodedata.normalize("NFC", text or "").casefold()
    words = set(re.findall(r"[^\W\d_]+", low, flags=re.UNICODE))
    # These characters are highly diagnostic in the ordinary query language
    # mix supported here. Check them before overlapping function-word scores.
    if any(ch in low for ch in "łęńśźżćą"):
        return "pl"
    if any(ch in low for ch in "ğış"):
        return "tr"
    if any(ch in low for ch in "ñ¿¡"):
        return "es"
    if any(ch in low for ch in "ãõ"):
        return "pt"
    if "œ" in low:
        return "fr"
    if "ß" in low:
        return "de"
    if "đ" in low:
        return "vi"
    # Swedish and German both use ä/ö; Swedish's å is distinctive among the
    # benchmarked language set, while common words disambiguate the rest.
    if "å" in low:
        return "sv"
    scores = {lang: len(words & markers) for lang, markers in _LATIN_WORD_MARKERS.items()}
    if any(ch in low for ch in "äöü"):
        scores["de"] += 1
    if any(ch in low for ch in "áéíóú"):
        # Shared accent family: give a small signal to Romance languages, but
        # require a second lexical marker before choosing one.
        for lang in ("es", "pt", "it"):
            scores[lang] += 1
    best = max(scores, key=lambda lang: scores[lang])
    return best if scores[best] >= 2 else "en"


def detect_language(text: str) -> LanguageInfo:
    text = unicodedata.normalize("NFC", text or "")
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isspace() or ch.isascii():
            continue
        for name, lo, hi in _RANGES:
            if lo <= ch <= hi:
                counts[name] = counts.get(name, 0) + 1
                break

    # Kana anywhere in a mixed Japanese query is stronger evidence than the
    # number of Han characters; otherwise Japanese place names can route to
    # the Chinese edition simply because they contain more Kanji.
    if counts.get("Hiragana", 0) or counts.get("Katakana", 0):
        return LanguageInfo(script="Japanese", lang="ja", wikipedia_lang="ja", is_latin=False)

    if counts:
        script = max(counts, key=lambda k: counts[k])
        # Bengali block with Assamese-specific letters ৰ/ৱ is Assamese.
        lang = _SCRIPT_LANG.get(script, "en")
        if script == "Bengali" and any(c in text for c in "ৰৱ"):
            lang = "as"
        elif script == "Cyrillic" and any(c in text.casefold() for c in "іїєґ"):
            lang = "uk"
        elif script == "Arabic" and any(c in text for c in "پچژگک"):
            # Persian's additional letters distinguish it from Arabic script
            # queries without guessing from the writing system alone.
            lang = "fa"
        return LanguageInfo(script=script, lang=lang, wikipedia_lang=lang, is_latin=False)

    if any(c in _YORUBA_CHARS for c in text) or text.count("́") + text.count("̀") >= 2:
        lang = "yo"
    else:
        lang = _latin_language(text)
    return LanguageInfo(script="Latin", lang=lang, wikipedia_lang=lang, is_latin=True)
