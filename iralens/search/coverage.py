# -*- coding: utf-8 -*-
"""Answer-coverage assessment for a search result set.

A search that returns *something* has not necessarily answered the
question. The audit found 45 queries whose results were "unrelated or
generic filler" while the system still reported `no_results_reason: "none"`
— retrieval had completed, the *question* had not been addressed.

This module turns "did we return anything?" into "did we return anything
that addresses the question?", so the caller can tell the difference and
so a research run cannot claim success on generic filler.

## How coverage is judged

The first version of this module counted how many of the question's
*content words* appeared in a result's title, snippet and URL, and called a
result set "low" below a third of them. That was wrong in both directions,
and the audit's own evidence shows how:

* It was too **strict**. A question is not a bag of words to be covered
  one-for-one. "Treaty of Yandabo exact date and terms primary source"
  names one subject ("Treaty of Yandabo") and then asks for two properties
  of it. A page that covers the subject *is* the answer; demanding that it
  also contain the words "primary" and "source" is demanding a coincidence
  of phrasing.
* It was too **lenient** in the other direction on the real failures:
  "global temperature 2025 WMO Copernicus estimates comparison" was
  "covered" by a page titled "GLOBLE" because one long word overlapped.

So coverage is judged on the question's **subject terms** — the part of the
question that names what the answer is about, with the request shape ("give
me the primary source", "official documentation", "exact date") removed —
and on the **distinguishing** ones among them: the terms that actually
identify the subject, as opposed to the words that are merely long or
common.

The test the audit asked for is the one that matters: *does this result
address the question, or is it generic filler?* A dictionary entry for
"why" and a homepage for "GLOBLE" do not address a question about Simpson's
paradox. The Wikipedia article "Assam" does address "What is the capital of
Assam?", even before its snippet says so.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .queryfocus import split_subject
from .querytext import parse_site_operator, term_set

#: Coverage ratios at or above this are "good": the results speak to most
#: of the question's distinguishing terms.
GOOD = 0.6
#: At or above this, but below GOOD: partial.
PARTIAL = 0.3

#: Terms that carry no discriminating power in a coverage judgement.
_GENERIC = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how i in into is it its of on or should so than that the their them there these
they this to was we were what when where which who why will with would you your
""".split())

#: A term of at least this many characters is treated as distinguishing:
#: long words are rarer than short ones, and the short ones are exactly the
#: ones that collide ("why", "how", "use").
_LONG_TERM = 9


def question_terms(question: str) -> List[str]:
    """The meaningful terms of a question, minus any `site:` scope."""
    _domains, cleaned = parse_site_operator(question or "")
    terms = [t for t in term_set(cleaned) if t not in _GENERIC]
    return sorted(terms)


def subject_terms(question: str) -> List[str]:
    """The terms that name what the question is *about*.

    The request shape is removed: "Treaty of Yandabo exact date and terms
    primary source" is about "Treaty of Yandabo". When the question has no
    separable request shape this is every content term.
    """
    subject, _request = split_subject(question or "")
    if not subject:
        return question_terms(question)
    terms = [t for t in term_set(subject) if t not in _GENERIC]
    return sorted(terms) or question_terms(question)


def distinguishing_terms(question: str) -> List[str]:
    """The subject terms that actually identify the subject.

    A term is distinguishing when it is rare by construction: a proper noun
    or acronym (capitalised in the question), a term carrying a digit or a
    version number, a term in a non-Latin script, or simply a long word.
    When a question has no such term, every subject term is used, because
    a question about "rongrorongo decipherment" is still about a specific
    subject even though nothing in it is capitalised.
    """
    _domains, cleaned = parse_site_operator(question or "")
    caps = _capitalised_terms(cleaned)
    terms = subject_terms(question)
    marked = [t for t in terms
              if (t in caps) or any(ch.isdigit() for ch in t)
              or any(ord(ch) > 127 for ch in t) or len(t) >= _LONG_TERM]
    return marked or terms


def _capitalised_terms(text: str) -> set:
    """The lowered forms of the terms that are capitalised in *text*."""
    from .queryfocus import _words_with_case

    out = set()
    words = _words_with_case(text or "")
    for index, word in enumerate(words):
        if not word:
            continue
        if index == 0:
            # A capitalised first word is usually just sentence case.
            if word.lower() in _GENERIC:
                continue
            if not word.isupper() and word[:1].isupper() and word[1:].islower():
                # "Treaty of Yandabo" — the first word may still be the
                # subject, so it is kept only if the sentence does not start
                # with an interrogative.
                if word.lower() in _QUESTION_STARTERS:
                    continue
        if word[:1].isupper() or (len(word) > 1 and word.isupper()):
            out.add(word.lower())
    return out


_QUESTION_STARTERS = frozenset("""
what which who whom whose when where why how is are was were do does did can
could should would will shall may might must explain describe define list
""".split())


def _field(result: Any, name: str) -> str:
    """Read a field from a result object *or* a result mapping."""
    if isinstance(result, dict):
        return str(result.get(name) or "")
    return str(getattr(result, name, "") or "")


def _result_text(result: Any) -> str:
    title = _field(result, "title")
    snippet = _field(result, "snippet")
    url = _field(result, "url")
    return f"{title} {snippet} {url}".lower()


def assess_coverage(question: str, results: Iterable[Any]) -> Dict[str, Any]:
    """How much of *question* do *results* address?

    Returns a dict with:

    ``status``      good | partial | low | unknown
    ``ratio``       0.0–1.0 — the best result's coverage of the subject
    ``matched``     how many distinguishing terms the best result carries
    ``total``       how many distinguishing terms there are
    ``note``        one honest sentence for the caller
    """
    distinguishing = distinguishing_terms(question or "")
    if not distinguishing:
        return {
            "status": "unknown",
            "ratio": 0.0,
            "matched": 0,
            "total": 0,
            "note": "the question has no distinctive terms to check coverage against",
        }
    items = list(results or [])
    if not items:
        return {
            "status": "low",
            "ratio": 0.0,
            "matched": 0,
            "total": len(distinguishing),
            "note": "no results were returned, so nothing addresses the question",
        }
    best_matched = 0
    best_ratio = 0.0
    for result in items:
        text = _result_text(result)
        matched = sum(1 for term in distinguishing if term in text)
        ratio = matched / len(distinguishing)
        if ratio > best_ratio:
            best_ratio = ratio
            best_matched = matched
    if best_ratio >= GOOD:
        status = "good"
        note = (f"the results address {best_matched} of {len(distinguishing)} "
                f"distinguishing terms of the question")
    elif best_ratio >= PARTIAL:
        status = "partial"
        note = (f"the results only partially address the question "
                f"({best_matched} of {len(distinguishing)} distinguishing terms); "
                f"treat them as leads")
    else:
        status = "low"
        note = (f"the results barely address the question "
                f"({best_matched} of {len(distinguishing)} distinguishing terms "
                f"matched); they are likely generic filler, not evidence")
    return {
        "status": status,
        "ratio": round(best_ratio, 4),
        "matched": best_matched,
        "total": len(distinguishing),
        "note": note,
    }


def coverage_is_usable(coverage: Optional[Dict[str, Any]]) -> bool:
    """True when the coverage verdict says the results can be used as evidence."""
    if not coverage:
        return True
    return str(coverage.get("status")) in ("good", "partial", "unknown")
