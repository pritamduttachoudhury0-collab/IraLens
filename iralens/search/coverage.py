# -*- coding: utf-8 -*-
"""Answer-coverage assessment for a search result set.

A search that returns *something* has not necessarily answered the
question. The audit found 45 queries whose results were "unrelated or
generic filler" while the system still reported `no_results_reason: "none"`
— retrieval had completed, the *question* had not been addressed.

This module turns "did we return anything?" into "did we return anything
that addresses the question?", so the caller can tell the difference and
so a research run cannot claim success on generic filler.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .querytext import parse_site_operator, term_set

#: Coverage ratios at or above this are "good": the results speak to most
#: of the question's terms.
GOOD = 0.6
#: At or above this, but below GOOD: partial.
PARTIAL = 0.3

#: Terms that carry no discriminating power in a coverage judgement.
_GENERIC = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how i in into is it its of on or should so than that the their them there these
they this to was we were what when where which who why will with would you your
""".split())


def question_terms(question: str) -> List[str]:
    """The meaningful terms of a question, minus any `site:` scope."""
    _domains, cleaned = parse_site_operator(question or "")
    terms = [t for t in term_set(cleaned) if t not in _GENERIC]
    return sorted(terms)


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
    ``ratio``       0.0–1.0 — the best result's term coverage
    ``matched``     how many question terms the best result carries
    ``total``       how many meaningful question terms there are
    ``note``        one honest sentence for the caller
    """
    terms = question_terms(question)
    if not terms:
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
            "total": len(terms),
            "note": "no results were returned, so nothing addresses the question",
        }
    best_matched = 0
    best_ratio = 0.0
    for result in items:
        text = _result_text(result)
        matched = sum(1 for term in terms if term in text)
        ratio = matched / len(terms)
        if ratio > best_ratio:
            best_ratio = ratio
            best_matched = matched
    if best_ratio >= GOOD:
        status = "good"
        note = f"the results address {best_matched} of {len(terms)} question terms"
    elif best_ratio >= PARTIAL:
        status = "partial"
        note = (f"the results only partially address the question "
                f"({best_matched} of {len(terms)} terms); treat them as leads")
    else:
        status = "low"
        note = (f"the results barely address the question "
                f"({best_matched} of {len(terms)} terms matched); they are likely "
                f"generic filler, not evidence")
    return {
        "status": status,
        "ratio": round(best_ratio, 4),
        "matched": best_matched,
        "total": len(terms),
        "note": note,
    }


def coverage_is_usable(coverage: Optional[Dict[str, Any]]) -> bool:
    """True when the coverage verdict says the results can be used as evidence."""
    if not coverage:
        return True
    return str(coverage.get("status")) in ("good", "partial", "unknown")
