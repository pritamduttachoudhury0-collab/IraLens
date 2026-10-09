# -*- coding: utf-8 -*-
"""Symbol-aware tokenization, quoted phrases, and query sanitizing (D-068)."""

import pytest

from halfiralens.search.querytext import (
    quoted_phrases,
    sanitize_query,
    strip_phrase_quotes,
    term_set,
    tokenize,
)
from halfiralens.search.reformulate import reformulate, _keywords


# --------------------------------------------------------------- tokenizing
def test_cpp_csharp_dotnet_tokens_survive():
    assert tokenize("C++ vector tutorial") == ["c++", "vector", "tutorial"]
    assert tokenize("C# events guide") == ["c#", "events", "guide"]
    assert set(tokenize("what's new in .NET 8")) >= {".net", "new"}
    assert "asp.net" in tokenize("asp.net routing")   # one token, dot kept inside
    assert ".net" in tokenize(".NET garbage collection")
    assert "f#" in tokenize("F# pipes")


def test_leading_dot_kept_only_for_dot_names():
    assert ".net" in term_set(".NET garbage collection")
    # trailing sentence dot is not a token character
    assert tokenize("see docs.") == ["see", "docs"]


def test_term_set_keeps_short_symbol_and_unicode_tokens():
    terms = term_set("C# 配置 go")
    assert "c#" in terms            # short but symbolic
    assert "配置" in terms           # unicode, 2 chars, still meaningful
    assert "go" not in terms         # short plain ASCII word: noise


def test_plain_ascii_noise_still_dropped():
    assert term_set("to fix a car") == {"fix", "car"}


def test_cjk_bigrams_enable_partial_matching():
    tokens = tokenize("Python 爬虫教程")
    assert "爬虫教程" in tokens
    assert "爬虫" in tokens          # bigram lets a 2-char query match
    assert term_set("爬虫") & term_set("Python 爬虫教程")


# ---------------------------------------------------------------- phrases
def test_quoted_phrases_extracted_and_stripped():
    text = 'difference between "std::vector" and "std::list" in c++'
    assert quoted_phrases(text) == ["std::vector", "std::list"]
    assert strip_phrase_quotes(text) == "difference between std::vector and std::list in c++"


def test_curly_quotes_are_phrases_too():
    assert quoted_phrases("search \u201cexact words\u201d now") == ["exact words"]


def test_phrase_dedup_and_whitespace_normalization():
    assert quoted_phrases('"a   b" and "a b"') == ["a b"]


# -------------------------------------------------------------- reformulate
def test_keywords_keep_symbols_and_phrases():
    kws = _keywords('best "std::map" tutorial for C++ beginners')
    assert '"std::map"' in kws
    assert "c++" in kws
    assert "for" not in kws


def test_reformulate_keywords_variant_preserves_cpp():
    out = reformulate("how to learn C++ vector syntax", max_queries=4)
    keywords = next(q for q in out if q["strategy"] == "keywords")
    assert "c++" in keywords["text"]
    assert " c " not in keywords["text"] + " "   # not mangled to bare 'c'


def test_reformulate_keywords_variant_preserves_quoted_phrase():
    out = reformulate('how to use "atomic operations" in C++', max_queries=4)
    keywords = next(q for q in out if q["strategy"] == "keywords")
    assert '"atomic operations"' in keywords["text"]


def test_reformulate_unicode_keywords():
    out = reformulate("如何学习 Python 爬虫", max_queries=4)
    keywords = next((q for q in out if q["strategy"] == "keywords"), None)
    assert keywords is not None
    assert "python" in keywords["text"]
    assert "爬虫" in keywords["text"]


# --------------------------------------------------------------- sanitize
def test_sanitize_strips_control_and_invisible_characters():
    raw = "so\u202elar query\u200b here\x00\x1f"
    cleaned = sanitize_query(raw)
    assert cleaned == "solar query here"
    assert "\u202e" not in cleaned and "\u200b" not in cleaned and "\x00" not in cleaned


def test_sanitize_collapses_whitespace():
    assert sanitize_query("  a \t\n b   c ") == "a b c"


def test_sanitize_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_query("   ")
    with pytest.raises(ValueError):
        sanitize_query("\u200b\u200b")


def test_sanitize_enforces_length_limit():
    long_query = "word " * 200  # 1000 chars
    with pytest.raises(ValueError) as exc:
        sanitize_query(long_query, max_chars=400)
    assert "too long" in str(exc.value)
    # just under the limit passes
    assert sanitize_query("abc def", max_chars=400) == "abc def"
