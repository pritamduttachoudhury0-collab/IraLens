# -*- coding: utf-8 -*-
"""Evidence evaluation: score sources, extract claims, normalize subjects.

Everything here is deterministic and inspectable. Source score is a weighted
sum of query relevance, domain authority, and recency, minus a penalty when
the content guard flags injection-like text. Claims are sentences from a
source that share at least one term with the question. A claim's subject is
its content terms, and its numbers and negation are recorded for contradiction
checks (contradictions.py).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..search.reformulate import STOPWORDS
from ..search.urls import domain_of
from ..settings import Settings
from ..search.ranking import authority, freshness

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+(?:\.\d+)?", re.UNICODE)
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?%?")
_NEGATION = frozenset({"not", "no", "never", "without", "cannot", "none", "neither", "nor",
                       "isn't", "doesn't", "wasn't", "aren't", "didn't", "won't", "don't", "hasn't"})
_GENERIC = frozenset({"report", "reports", "said", "says", "news", "according", "also", "more", "most",
                      "about", "their", "there", "which", "would", "could", "other", "using"})


def content_terms(text: str) -> List[str]:
    """Ordered, unique content words (length >= 3, no stopwords)."""
    seen: List[str] = []
    for tok in _WORD.findall(text or ""):
        low = tok.lower().strip("'-")
        if len(low) < 3 or low in STOPWORDS or low in _GENERIC or low.isdigit():
            continue
        if low not in seen:
            seen.append(low)
    return seen


def question_terms(question: str) -> Set[str]:
    return set(content_terms(question))


def relevance(question: str, *texts: str) -> float:
    q = question_terms(question)
    if not q:
        return 0.0
    body = set(content_terms(" ".join(texts)))
    return round(len(q & body) / len(q), 4)


def score_source(
    question: str,
    *,
    url: str,
    title: str,
    snippet: str,
    content: str,
    published_at: Optional[str],
    flags: Sequence[str],
    settings: Settings,
    today: date,
) -> Tuple[float, Dict[str, float]]:
    rel = relevance(question, title, snippet, content[:4000])
    qual = authority(url, settings.authority_weights, settings.default_authority)
    rec = freshness(published_at, today, settings.freshness_half_life_days, settings.neutral_freshness)
    weights = {"relevance": settings.evidence_weight_relevance,
               "quality": settings.evidence_weight_quality,
               "recency": settings.evidence_weight_recency}
    total = sum(weights.values()) or 1.0
    score = (rel * weights["relevance"] + qual * weights["quality"] + rec * weights["recency"]) / total
    breakdown = {"relevance": rel, "quality": qual, "recency": rec}
    if any(f.startswith("prompt_injection") for f in flags):
        score -= settings.injection_penalty
        breakdown["injection_penalty"] = settings.injection_penalty
    score = round(max(0.0, min(1.0, score)), 4)
    return score, breakdown


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    out = []
    for part in parts:
        part = part.strip()
        words = part.split()
        if 6 <= len(words) <= 40:
            out.append(part)
    return out


def _measurements(sentence: str) -> List[str]:
    """Numbers that are measurements. Four-digit years are context, not values:
    comparing them would flag every pair of sources that cite different years."""
    out = []
    for tok in _NUMBER.findall(sentence):
        if re.fullmatch(r"(19|20)\d{2}", tok):
            continue
        out.append(tok)
    return out


def extract_claims(question: str, text: str, limit: int = 10) -> List[Dict[str, object]]:
    """Sentences that mention the question's terms, as claim candidates."""
    q = question_terms(question)
    found: List[Dict[str, object]] = []
    for sentence in split_sentences(text):
        terms = content_terms(sentence)
        if not q & set(terms):
            continue
        low_tokens = {t.lower().strip("'") for t in re.findall(r"[A-Za-z']+", sentence)}
        found.append({
            "text": sentence,
            "subject_terms": terms[:8],
            "numbers": _measurements(sentence),
            "negated": bool(low_tokens & _NEGATION),
        })
        if len(found) >= limit:
            break
    return found


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def source_domain(url: str) -> str:
    return domain_of(url)
