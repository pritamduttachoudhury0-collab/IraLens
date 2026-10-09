# -*- coding: utf-8 -*-
"""Deterministic query reformulation.

Turns one question into up to `max_queries` search queries, each tagged with
the strategy that produced it. The rules are plain text transforms, so the
expansion is reproducible and testable. An LLM-based expander can be plugged
in through research/interfaces.py, but it is not required.
"""

from __future__ import annotations

import re
from typing import Dict, List, Mapping

from .querytext import quoted_phrases, tokenize

STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how i in into is it its of on or should so than that the their them there these
they this to was we were what when where which who why will with would you your
""".split())

#: Small default synonym map. Config can replace it via `search_synonyms`.
DEFAULT_SYNONYMS: Dict[str, List[str]] = {
    "buy": ["purchase"],
    "cheap": ["affordable", "low cost"],
    "car": ["automobile"],
    "doctor": ["physician"],
    "fix": ["repair"],
    "price": ["cost"],
}


def _keywords(text: str) -> List[str]:
    """Keyword form of the query, symbol-aware.

    Technology tokens keep their symbols (`c++`, `c#`, `.net`, `f#`), quoted
    phrases stay quoted so engines treat them as exact phrases, and stopword
    pruning only applies to plain words. Unicode terms pass through.
    """
    kept: List[str] = []
    for phrase in quoted_phrases(text):
        quoted = f'"{phrase}"'
        if quoted not in kept:
            kept.append(quoted)
    for token in tokenize(text):
        is_symbol_token = any(ch in "+#." for ch in token)
        if not is_symbol_token:
            if token in STOPWORDS:
                continue
            if len(token) <= 2 and token.isascii():
                continue
        if token not in kept:
            kept.append(token)
    return kept


def reformulate(
    question: str,
    max_queries: int = 3,
    synonyms: Mapping[str, List[str]] = DEFAULT_SYNONYMS,
) -> List[Dict[str, str]]:
    """Return [{'text': ..., 'strategy': ...}] with the original query first.

    Strategies, in order: original, keywords, sub_questions, synonym. Duplicate
    texts (case-insensitive) are dropped, and the list is capped at max_queries.
    """
    question = " ".join((question or "").split())
    if not question:
        return []
    candidates: List[Dict[str, str]] = [{"text": question, "strategy": "original"}]

    keywords = _keywords(question)
    if keywords:
        candidates.append({"text": " ".join(keywords), "strategy": "keywords"})

    parts = [p.strip(" ?.;:") for p in re.split(r"\?|;| and | vs\.? | versus ", question)]
    subs = [p for p in parts if len(p.split()) >= 3]
    if len(subs) > 1:
        for part in subs:
            candidates.append({"text": part, "strategy": "sub_question"})

    swapped = question
    for term, alts in synonyms.items():
        if re.search(rf"\b{re.escape(term)}\b", swapped, re.IGNORECASE) and alts:
            swapped = re.sub(rf"\b{re.escape(term)}\b", alts[0], swapped, count=1, flags=re.IGNORECASE)
            break
    if swapped != question:
        candidates.append({"text": swapped, "strategy": "synonym"})

    seen = set()
    out: List[Dict[str, str]] = []
    for cand in candidates:
        key = cand["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
        if len(out) >= max(1, max_queries):
            break
    return out
