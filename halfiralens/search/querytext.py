# -*- coding: utf-8 -*-
"""Query-text handling: symbol-aware tokenization, quoted phrases, sanitizing.

Plain ``\\w+`` tokenization mangles technology names that agents search for
every day: ``C++`` becomes ``c``, ``C#`` becomes ``c``, ``.NET`` becomes
``net`` (indistinguishable from the word "net"), ``F#`` becomes ``f``. That
both inflates relevance for pages that merely mention the bare letter and
destroys keyword reformulations.

The tokenizer here keeps the language-symbol characters ``+ # .`` attached to
the words they belong to, so the same query and the same title always produce
the same tokens:

    "C++ vector tutorial"  -> {"c++", "vector", "tutorial"}
    "asp.net routing"      -> {"asp.net", "routing"}
    ".NET 8 releases"      -> {".net", "releases"}   (leading dot kept)

Quoted phrases ("exact match") are recognized separately so reformulation can
keep them intact and ranking can credit phrase coverage.

Query sanitizing strips control and invisible characters (bidi overrides can
make a query look different from what is sent) and enforces a length limit.
"""

from __future__ import annotations

import re
from typing import List

#: Runs of word characters plus the technology symbols ``+ # . _ -``, and any
#: non-ASCII character (Unicode queries must not fall on the floor).
_RUN_RE = re.compile(r"(?:[A-Za-z0-9._+#\-]|[^\x00-\x7f\s])+")

#: Ranges treated as CJK for bigram tokenization (Unified Ideographs incl.
#: Ext-A, Hiragana/Katakana block, Hangul, Compatibility Ideographs).
_CJK_RANGES = (
    ("\u3400", "\u4dbf"), ("\u3040", "\u30ff"), ("\u4e00", "\u9fff"),
    ("\uac00", "\ud7af"), ("\uf900", "\ufaff"),
)


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)
#: Quoted phrases: straight or curly double quotes, 2..120 chars inside.
_QUOTED_RE = re.compile(r'"([^"]{2,120})"|\u201c([^\u201d]{2,120})\u201d')
#: Control characters (except whitespace handled separately) and invisible
#: formatting characters (zero-width spaces, bidi overrides).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INVISIBLE_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)

#: Characters that make a token a "technology symbol" token.
_SYMBOL_CHARS = "+#."

#: Default cap on query length (settings can override). Long pasted blobs are
#: almost always a mistake and some engines reject or misparse them.
DEFAULT_MAX_QUERY_CHARS = 400


def _normalize_run(run: str) -> str:
    """Normalize one raw run to a token, or '' to drop it."""
    token = run.strip("-_").rstrip(".")
    if not token:
        return ""
    # A leading dot is kept only for dot-prefixed names like ".net" or
    # ".gitignore"; otherwise it is sentence punctuation.
    if token.startswith("."):
        if not re.match(r"^\.[A-Za-z][A-Za-z0-9]*$", token):
            token = token.lstrip(".")
    if not token or not any(ch.isalnum() for ch in token):
        return ""
    return token.lower()


def tokenize(text: str) -> List[str]:
    """Tokenize text keeping C++/C#/.NET-style tokens intact (order kept).

    CJK runs have no word boundaries, so they are kept whole *and* split into
    overlapping character bigrams — the standard n-gram trick that lets a
    query term like 爬虫 match a page containing 爬虫教程.
    """
    out: List[str] = []
    for run in _RUN_RE.findall(text or ""):
        if len(run) >= 2 and all(_is_cjk(ch) for ch in run):
            candidates = [run] + [run[i:i + 2] for i in range(len(run) - 1)]
        else:
            candidates = [_normalize_run(run)]
        for token in candidates:
            if token and token not in out:
                out.append(token)
    return out


def term_set(text: str) -> set:
    """Set of meaningful tokens: symbols-bearing or non-ASCII tokens are kept
    even when short ('c#', '配置'); plain ASCII words follow the classic
    length>2 rule so 'the'/'of' noise stays out."""
    terms = set()
    for token in tokenize(text):
        if (
            len(token) > 2
            or any(ch in _SYMBOL_CHARS for ch in token)
            or any(ord(ch) > 127 for ch in token)
        ):
            terms.add(token)
    return terms


def quoted_phrases(text: str) -> List[str]:
    """Return the quoted phrases in *text*, whitespace-normalized, order kept."""
    phrases: List[str] = []
    for match in _QUOTED_RE.finditer(text or ""):
        phrase = " ".join((match.group(1) or match.group(2) or "").split())
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def strip_phrase_quotes(text: str) -> str:
    """Replace each quoted phrase by its unquoted content."""
    return _QUOTED_RE.sub(lambda m: m.group(1) or m.group(2), text or "")


def sanitize_query(text: str, max_chars: int = DEFAULT_MAX_QUERY_CHARS) -> str:
    """Strip control/invisible characters and enforce the length limit.

    Returns the whitespace-normalized query. Raises ValueError when the query
    is empty after cleaning or longer than *max_chars*.
    """
    cleaned = _CONTROL_RE.sub("", str(text or ""))
    cleaned = _INVISIBLE_RE.sub("", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        raise ValueError("search requires a non-empty query")
    if max_chars and len(cleaned) > max_chars:
        raise ValueError(
            f"query is too long ({len(cleaned)} chars); the limit is {max_chars} — "
            "shorten it or split it into several searches"
        )
    return cleaned
