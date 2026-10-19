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

from .queryfocus import match_request_phrase
from .querytext import parse_site_scopes, quoted_phrases, tokenize

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

    A `site:` scope survives: it is carried through verbatim instead of
    being flattened into the words "site" and the bare host.
    """
    kept: List[str] = []
    scope_domains, cleaned = parse_site_scopes(text)
    for phrase in quoted_phrases(cleaned):
        quoted = f'"{phrase}"'
        if quoted not in kept:
            kept.append(quoted)
    for token in tokenize(cleaned):
        is_symbol_token = any(ch in "+#." for ch in token)
        if not is_symbol_token:
            if token in STOPWORDS:
                continue
            if len(token) <= 2 and token.isascii():
                continue
        if token not in kept:
            kept.append(token)
    for domain in scope_domains:
        scoped = f"site:{domain}"
        if scoped not in kept:
            kept.append(scoped)
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

    # The subject of the question, without the request shape. A provider
    # given "Python asyncio TaskGroup cancellation ExceptionGroup official
    # documentation" matches loosely; given the subject alone it answers
    # the question. The full question is still sent — this is an *additional*
    # variant, never a replacement for it.
    split = match_request_phrase(question)
    scope_prefixes: List[str] = []
    if split is None:
        # A `site:` scope is a constraint, not a request, so the splitter
        # leaves a scoped query alone — but the shape inside a scoped query
        # is still a shape. The scope is taken off, the rest is split, and
        # the scope is put back on the variant: the constraint survives
        # exactly as the caller wrote it.
        scopes, cleaned = parse_site_scopes(question)
        if scopes:
            split = match_request_phrase(cleaned)
            scope_prefixes = [f"site:{scope}" for scope in scopes]
    if split:
        subject, _shape = split
        subject_terms = _keywords(subject) if subject else []
        # A two-word subject collapses onto whatever its head term is:
        # "capital assam" is a search for Capital One. Only a subject with
        # real substance is worth a variant of its own.
        if len(subject_terms) >= 3:
            subject_text = " ".join(subject_terms)
            if scope_prefixes:
                subject_text = " ".join(scope_prefixes + subject_terms)
            if subject_text.lower() != question.lower():
                candidates.append({"text": subject_text, "strategy": "focus"})

    # The plain keyword form of the whole question. It is a variant of its
    # own, not a replacement: the full question is always sent as well.
    #
    # A *short* keyword form is a liability, not a variant. Measured over
    # real provider results, the two-word form of "What is the capital of
    # Assam?" is "capital assam", and a search for that returns Capital One
    # and a dictionary entry — zero on-target results, where the question
    # itself returns ten. A keyword form is only worth sending when it
    # still carries enough of the question to stand on its own.
    keywords = _keywords(question)
    if len(keywords) >= 3:
        keyword_text = " ".join(keywords)
        if keyword_text.lower() != question.lower():
            candidates.append({"text": keyword_text, "strategy": "keywords"})

    # Jurisdiction-biased variant (D2): a procedure/statistic question naming
    # a place gets one site-biased query so the official page is *retrieved*,
    # not merely re-ranked. General gazetteer domains only.
    from .intent import classify_query
    intent = classify_query(question)
    if intent.jurisdiction_domains and intent.answer_type in ("procedure", "statistic", "current_event"):
        candidates.append({"text": f"{question} site:{intent.jurisdiction_domains[0]}",
                           "strategy": "jurisdiction"})

    parts = [p.strip(" ?.;:") for p in re.split(r"\?|;| and | vs\.? | versus ", question)]
    subs = [p for p in parts if len(p.split()) >= 3]
    if len(subs) > 1:
        # A `site:` scope is a constraint on the whole question: every
        # sub-question inherits it, or a split quietly widens the search.
        scope_domains, _ = parse_site_scopes(question)
        scope = " ".join(f"site:{domain}" for domain in scope_domains)
        for part in subs:
            text = f"{part} {scope}".strip() if scope else part
            candidates.append({"text": text, "strategy": "sub_question"})

    swapped = question
    for term, alts in synonyms.items():
        if re.search(rf"\b{re.escape(term)}\b", swapped, re.IGNORECASE) and alts:
            swapped = re.sub(rf"\b{re.escape(term)}\b", alts[0], swapped, count=1, flags=re.IGNORECASE)
            break
    if swapped != question:
        candidates.append({"text": swapped, "strategy": "synonym"})

    # The request-bearing query should be followed by the variants with the
    # strongest retrieval intent. In particular, an official/jurisdiction
    # form and a decomposed sub-question are more informative than a generic
    # bag-of-words rewrite. With the default three-query budget, keywords used
    # to crowd those higher-value forms out of the plan.
    priority = {"original": 0, "focus": 1, "jurisdiction": 2,
                "sub_question": 3, "keywords": 4, "synonym": 5}
    candidates = [item for _index, item in sorted(
        enumerate(candidates),
        key=lambda pair: (priority.get(pair[1]["strategy"], 99), pair[0]),
    )]

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
