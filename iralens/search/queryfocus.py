# -*- coding: utf-8 -*-
r"""Split a question into its **subject** and its **request shape**.

Most research questions have the same two-part shape:

    "exoplanet atmosphere spectroscopy replication failures primary literature"
     \_________________ subject _________________/ \__ request shape __/

The *subject* is what the answer is about. The *request shape* asks for a
particular kind of source: the official documentation, the primary source,
the exact date, a worked example, the original paper. Search engines handle
the two parts very differently — a provider given the whole sentence treats
it as a natural-language question and matches loosely, and answers
"Welcome to Python.org" for a question about ``asyncio.TaskGroup``.

So the subject is sent on its own as an *additional* query variant. The
original question is still sent verbatim — the audit was explicit that the
full query must not be silently dropped — and the request shape is then
applied where it belongs: at *ranking* time, preferring the kind of source
the question asked for.

The split is lexical: a lexicon of request words marks the shape, and the
shape is the **suffix** of the question that carries it. Scanning from the
end is what makes the rule safe. A request shape is a phrase that qualifies
the subject ("… *official documentation*"), so it sits at the end; a request
word that appears in the *middle* of a subject ("Simpson paradox
**examples**", "prime number theorem **error** term") is part of the
subject and must not cut it. Scanning backwards finds the last request word
and everything after it, and the subject is what precedes it.

The request-word lexicon is derived from data, not guessed:
`benchmarks/derive_request_markers.py` learns it from the audit corpus's own
topic/suffix decomposition, on a training half, and reports the accuracy
on a held-out half and on the hand-authored queries it never saw.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from .querytext import parse_site_scopes, tokenize

#: Words that ask for a *kind of source* rather than naming a subject.
#:
#: Derived from the audit corpus by `benchmarks/derive_request_markers.py`
#: and kept here so the module has no data dependency at run time. A word is
#: only a marker when it is not the first content word of the question, so a
#: subject that happens to contain one of them ("primary school enrollment
#: statistics") is not truncated.
REQUEST_MARKERS: frozenset = frozenset("""
academic accounts against alternatives announcement archive article articles arxiv behavior breaking browser case cases
caveats change changes check code comment comments common comparability compare comparison competing concern conflicting
consequences conservation contemporary context corroborated counterexample counterexamples court csv current dashboard data dataset datasets
date dates debate debugging definition definitions disambiguation discrepancy disputed documentation documented documents doi downloadable
earliest economic effect encyclopedia error errors evidence exact example examples experiment experimental explanation explanations
fact facts factual failing failure failures fee fees fix gaps github government guidance guide
guideline guidelines historical histories implications independent industry interactive interpretation interpretations intervals issue issues known
later latest lecture legal legislation library limit limitations list literal literature logs main metadata
methodology methods migration minimal misconception misconceptions navigation note notes numeric official operations original page
paper papers pdf peer primary principles proof public published recent recommended record records reference
references regression release releases reliable rendered report reports reproduce reproducibility reproducible research retrospective review
reviewed reviews revisions rigorous safety scoping section sections semantics simplified source sources specific spelling
statistics statute studies study subscription superseding systematic technical terminology text texts trial troubleshooting two
uncertainty university update usual variants version versions versus wiki without worked
""".split())

#: Words that may appear *inside* a request shape without starting one.
#:
#: A wider set than `REQUEST_MARKERS`: the shape's first word is often not
#: a marker ("WHO guidance", "official policy", "software context"), so a
#: shape has to be allowed to begin with a shape-word that is not a marker.
SHAPE_WORDS: frozenset = frozenset("""
academic accessibility account accounts against alternative alternatives announcement archive article behavior breaking browser case
causal caveats changes check claims common comparability compare comparison competing conflicting conservation contemporary context
conventions corroborated counterexample court csv current dashboard dataset date dates debugging definition definitions disambiguation
disputed documentation documented documents downloadable earliest effect eligibility encyclopedia error errors evidence exact example
exceptions experimental explanation explanations fact facts factual failing failure failures fees fix github government
guidance guide guideline historical histories how implication implications independent indian industry interactive interpretations intervals
issue known later latest lecture limitations literature main meaning measurement metadata methodology methods migration
minimal misconceptions names navigation notes official pdf peer primary proof quality recent recommended record
reference references regression release reliable rendered replication reporting reproducible research retry review reviewed reviews
revisions rigorous scientific section security simplified software source sources specific spelling statute studies study
systematic technical terminology text troubleshooting two uncertainty university update usual variants version what where
which who why wikipedia without worked year
""".split())

#: Whole request shapes, as normalised word sequences.
#:
#: A phrase is matched at the *end* of a question. Matching a phrase is far
#: more precise than matching single words: "official policy" and "public
#: access" are shapes, while "policy" and "access" are ordinary words that
#: a subject may well contain.
#:
#: The learned ones come from `benchmarks/derive_request_markers.py`; the
#: hand-written ones cover the shapes a researcher writes that the corpus
#: does not happen to contain.
REQUEST_PHRASES: frozenset = frozenset(
    phrase.strip()
    for phrase in """
academic source references|
archive documents|
arxiv|
as of 17 october 2026 official source|
browser rendered content accessibility|
common factual errors|
common misconceptions and evidence|
compare explanations from two independent references|
compare independent estimates|
compare two independent systematic reviews|
competing historical interpretations|
conflicting accounts evidence|
conflicting definitions conventions|
conflicting estimates methodology|
conflicting guidelines explanation|
conservation or archive official record|
contemporary accounts versus later histories|
csv api metadata|
current rules october 2026|
debugging production error|
disambiguation|
disputed facts reliable sources|
doi|
earliest documented date|
encyclopedia fact check|
evidence for and against causal effect|
evidence quality systematic review|
exact date and terms|
exact dates primary sources|
exceptions court interpretation|
experimental evidence and limitations|
explanation with primary sources|
failure case study|
fees and eligibility official source|
github issue regression|
government data versus industry claims|
government reference source|
indian government guideline|
interactive dashboard data source|
known limitations 2026|
latest guideline date|
latest official update october 2026|
latest release|
latest release date|
latest release date primary source|
latest year methodology|
limits of the usual simplified explanation|
main article text without navigation|
measurement methods uncertainty|
measurement uncertainty|
migration breaking changes|
minimal failing example fix|
minimal reproducible example|
official dataset download|
official documentation|
official policy|
official procedure|
official reference|
official source|
original paper|
original paper arxiv|
original paper doi|
pdf|
peer reviewed comparison methods|
primary documentation|
primary source|
primary studies with uncertainty intervals|
proof assumptions limitations|
public access alternatives|
recent peer reviewed research 2025 2026|
recent reporting corroborated by official announcement|
recommended retry behavior|
references|
replication failures primary literature|
reproducible error documentation|
revisions comparability caveats|
rigorous explanation|
safety limits primary source|
scientific meaning|
security implications|
september 2026 versus october 2026 changes|
software context|
source methodology and downloadable data|
spelling variants historical names|
standards technical guide|
statute section primary source|
systematic review|
terminology definition sources|
troubleshooting guide|
university lecture notes pdf|
version specific known issue|
who guidance|
wikipedia references|
worked counterexample|
worked numerical example|
""".split("|")
    if phrase.strip()
)

#: Function words that may appear *inside* a request shape without starting
#: a new one.
CONNECTIVES: frozenset = frozenset("""
and as by for from in into of on or that the to with within
""".split())

#: Words that open a question rather than name a subject.
_QUESTION_WORDS = frozenset("""
what which who whom whose when where why how is are was were do does did can
could should would will shall may might must explain describe define list
""".split())

#: Month names, so a request shape like "current rules October 2026" is not
#: cut in half by its date.
_MONTHS = frozenset("""
january february march april may june july august september october november
december jan feb mar apr jun jul aug sep sept oct nov dec
""".split())


def _words_with_case(text: str) -> List[str]:
    """Word-ish runs of *text*, keeping their original case."""
    from .querytext import _RUN_RE

    return [m.group(0) for m in _RUN_RE.finditer(text or "")]


def _is_bare_year(word: str) -> bool:
    """Is this token a bare year, like "2026"?

    A bare year at the end of a question is a temporal qualifier ("as of
    2026"), not part of the subject and not a request for a kind of source.
    """
    stripped = word.strip(".,;:!?()[]\"'")
    return (len(stripped) == 4 and stripped.isdigit()
            and 1900 <= int(stripped) <= 2099)


def _is_date_like(word: str) -> bool:
    """Is this token part of a date or a version number?"""
    low = word.lower()
    if not low:
        return False
    if low in _MONTHS:
        return True
    if any(ch.isdigit() for ch in low):
        return True
    return False


def _marker(word: str) -> bool:
    return word.lower() in REQUEST_MARKERS


def _shape_word(word: str) -> bool:
    """Can this word sit inside a request shape?"""
    low = word.lower()
    return (low in SHAPE_WORDS or low in REQUEST_MARKERS
            or low in CONNECTIVES or _is_date_like(word))


def _normalise_words(words: List[str]) -> List[str]:
    """Lower-cased, punctuation-stripped words."""
    out = []
    for word in words:
        cleaned = word.strip(".,;:!?()[]\"'")
        if cleaned:
            out.append(cleaned.lower())
    return out


def _match_phrase(words: List[str]) -> Tuple[int, List[str]] | None:
    """Match a known request shape at the end of *words*.

    Returns ``(index where the shape starts, the shape's words)`` or None.
    """
    plain = _normalise_words(words)
    best: Tuple[int, List[str]] | None = None
    for phrase in REQUEST_PHRASES:
        phrase_words = phrase.split()
        if not phrase_words or len(phrase_words) >= len(plain):
            continue
        if all(_is_date_like(word) for word in phrase_words):
            # A date is part of the subject, never a request: "… October
            # 2026" must not be split so that "2026" becomes the shape.
            continue
        if plain[-len(phrase_words):] != phrase_words:
            continue
        begin = len(words) - len(phrase_words)
        if best is None or begin < best[0]:
            best = (begin, words[begin:])
    return best


def match_request_phrase(text: str) -> Tuple[str, str] | None:
    """Split *text* at a **known** request shape, or return None.

    This is the high-precision half of :func:`split_subject`. A known shape
    is a phrase the request lexicon has actually seen, so a question that
    merely *contains* a request word ("what is the price of a used car and
    how to fix it") is not split. The focus query variant uses this, so it
    only fires on a question that really does ask for a particular kind of
    source.
    """
    raw = " ".join((text or "").split())
    if not raw:
        return None
    scopes, cleaned = parse_site_scopes(raw)
    if scopes:
        return None
    from .querytext import _RUN_RE

    spans = [m.group(0) for m in _RUN_RE.finditer(cleaned)]
    if len(spans) < 2:
        return None
    matched = _match_phrase(spans)
    if matched is None:
        return None
    begin, _shape_words = matched
    subject, shape = _cut(cleaned, _spans(cleaned), begin)
    if not shape:
        return None
    return subject, shape


def _spans(text: str) -> List[tuple]:
    from .querytext import _RUN_RE

    return [(m.group(0), m.start(), m.end()) for m in _RUN_RE.finditer(text)]


def split_subject(text: str) -> Tuple[str, str]:
    """Split *text* into ``(subject, request_shape)``.

    The request shape is the suffix of the question that carries a request
    word or matches a known request phrase. A query with a `site:` scope is
    never split: the scope is a constraint, not a request, and it has to
    survive into the focus variant unchanged.
    """
    raw = " ".join((text or "").split())
    if not raw:
        return "", ""
    domains, cleaned = parse_site_scopes(raw)
    if domains:
        # A scoped query has nothing to split off: the scope is the
        # constraint, and the rest of the query is the subject.
        return raw, ""

    from .querytext import _RUN_RE

    spans = [(m.group(0), m.start(), m.end()) for m in _RUN_RE.finditer(cleaned)]
    words = [s[0] for s in spans]
    if len(words) < 2:
        return raw, ""

    # A whole request shape at the end of the question is the most precise
    # signal there is, so it is tried first.
    matched = _match_phrase(words)
    if matched is not None:
        begin, _shape_words = matched
        return _cut(cleaned, spans, begin)

    # If the question *starts* with a request word, it is about that kind
    # of request ("primary school enrollment statistics 2024" is a subject
    # that happens to begin with "primary"), so it is not split.
    if _first_content_word(words) in REQUEST_MARKERS:
        return raw, ""

    # Scan backwards for the last request word, skipping a trailing date.
    start = len(words)
    found = False
    for index in range(len(words) - 1, -1, -1):
        word = words[index]
        if _marker(word):
            start = index
            found = True
            break
        if _is_date_like(word) or word.lower() in CONNECTIVES:
            continue
        break
    if not found:
        # No request word anywhere. A trailing bare year is a temporal
        # qualifier ("as of 2026"): it is not the subject, and it says
        # nothing about the kind of source wanted, but it is separable.
        if len(words) > 1 and _is_bare_year(words[-1]):
            return _cut(cleaned, spans, len(words) - 1)
        # Otherwise the trailing date is part of the subject, not a shape
        # of its own.
        return raw, ""

    if start == 0:
        # The whole question is a request shape ("primary sources?"). There
        # is no subject to search for on its own.
        return raw, ""

    # Extend the shape forward over the words that belong to it.
    begin = start
    while begin > 0 and _shape_word(words[begin - 1]):
        begin -= 1

    return _cut(cleaned, spans, begin)


def _first_content_word(words: List[str]) -> str:
    """The first word of the question that carries content, lower-cased."""

    for index, word in enumerate(words):
        if index == 0 and word.lower() in _QUESTION_WORDS:
            continue
        return word.lower()
    return ""


def _cut(cleaned: str, spans: List[tuple], begin: int) -> Tuple[str, str]:
    """Split *cleaned* at word *begin*, keeping the original text."""
    if begin <= 0 or begin >= len(spans):
        return cleaned.strip(), ""
    offset = spans[begin][1]
    subject = cleaned[:offset].strip().rstrip(":;,-— ").strip()
    shape = cleaned[offset:].strip()
    if not subject or not shape:
        return cleaned.strip(), ""
    return subject, shape


def subject_terms(text: str) -> List[str]:
    """The subject's content tokens, in order, with duplicates removed."""
    subject, _shape = split_subject(text)
    if not subject:
        return []
    seen: List[str] = []
    for token in tokenize(subject):
        if token not in seen:
            seen.append(token)
    return seen


def request_markers(text: str) -> List[str]:
    """The request-shape words present in *text* (lowercased, in order)."""
    _subject, shape = split_subject(text)
    if not shape:
        return []
    out: List[str] = []
    for word in _words_with_case(shape):
        low = word.lower()
        if low in REQUEST_MARKERS and low not in out:
            out.append(low)
    return out


_REQUEST_PHRASE_RE = re.compile(
    r"\b(officially|official\s+documentation|primary\s+source|original\s+paper|"
    r"peer[\s-]reviewed|preprint|latest|most\s+recent|current)\b", re.IGNORECASE)


def wants(text: str, marker: str) -> bool:
    """Does *text* ask for a source of the given *marker* kind?

    ``wants(q, "official")`` is true for "official documentation",
    ``wants(q, "primary")`` for "primary source", and so on.
    """
    low = (text or "").lower()
    if marker in ("official", "documentation"):
        if any(word in low for word in ("official", "documentation", "docs")):
            return True
    if marker in ("primary", "source", "original", "paper", "preprint",
                  "peer reviewed", "peer-reviewed", "doi", "arxiv", "literature"):
        if any(word in low for word in ("primary", "original paper", "preprint",
                                        "peer-reviewed", "peer reviewed", "doi",
                                        "arxiv", "primary literature")):
            return True
    if marker in ("latest", "current", "today", "recent", "release", "version"):
        if any(word in low for word in ("latest", "current", "today", "recent",
                                        "as of", "now", "release", "version")):
            return True
    if marker in ("exact", "precise", "date"):
        if any(word in low for word in ("exact", "precise")):
            return True
    if marker in ("example", "examples", "worked", "numerical", "counterexample"):
        if any(word in low for word in ("worked example", "numerical example",
                                        "worked numerical", "counterexample",
                                        "for example")):
            return True
    if marker in ("comparison", "compare", "versus", "versus"):
        if any(word in low for word in ("comparison", "compare", "versus", " vs ")):
            return True
    if marker in ("error", "errors", "debugging", "troubleshooting", "failing"):
        if any(word in low for word in ("error", "debug", "troubleshoot", "failing")):
            return True
    if marker in ("data", "dataset", "statistics", "estimate", "estimates",
                  "figures", "values", "csv"):
        if any(word in low for word in ("dataset", "data", "statistics",
                                        "estimate", "figures", "csv")):
            return True
    if marker in ("government", "official", "guideline", "statute", "legal"):
        if any(word in low for word in ("government", "guideline", "statute",
                                        "legal", "legislation")):
            return True
    return False
