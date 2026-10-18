# -*- coding: utf-8 -*-
"""The subject/request split and the `focus` query variant.

The audit's 3,000-query corpus is `<topic> + <template suffix>` and records
the topic, so the split that the `focus` variant depends on can be measured
against ground truth rather than eyeballed. These tests pin that behaviour,
and pin the two rules the audit insisted on:

  * the full question is always sent — the focus variant is *additional*;
  * the split is conservative — a question with no separable request shape
    is not split at all.
"""

import json
from pathlib import Path

import pytest

from iralens.search.coverage import assess_coverage, distinguishing_terms
from iralens.search.querytext import parse_site_scopes, site_scoped_query
from iralens.search.queryfocus import split_subject
from iralens.search.reformulate import reformulate
from iralens.search.urls import scope_matches


REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "benchmarks" / "audit3000" / "queries.jsonl"


def _corpus():
    return [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ---------------------------------------------------------------- the split
@pytest.mark.parametrize("question, subject, shape", [
    ("Python asyncio TaskGroup cancellation ExceptionGroup official documentation",
     "Python asyncio TaskGroup cancellation ExceptionGroup", "official documentation"),
    ("exoplanet atmosphere spectroscopy replication failures primary literature",
     "exoplanet atmosphere spectroscopy", "replication failures primary literature"),
    ("nginx reverse proxy websocket version specific known issue",
     "nginx reverse proxy websocket", "version specific known issue"),
    ("vitamin D supplementation adults WHO guidance",
     "vitamin D supplementation adults", "WHO guidance"),
    ("how earthquakes are measured: common misconceptions and evidence",
     "how earthquakes are measured", "common misconceptions and evidence"),
])
def test_a_request_shape_is_split_off_the_subject(question, subject, shape):
    assert split_subject(question) == (subject, shape)


@pytest.mark.parametrize("question", [
    "What is the capital of Assam?",
    "primary school enrollment statistics 2024",
    "Rongorongo decipherment scholarly consensus",
    "primary sources?",
])
def test_a_question_with_no_request_shape_is_not_split(question):
    """Conservative: no shape, no split. A subject that merely contains a
    request word ("primary school enrollment") must not be truncated."""
    subject, shape = split_subject(question)
    assert shape == ""
    assert subject == question


def test_a_site_scope_is_never_split_off():
    """The scope is a constraint, not a request, and it has to survive into
    the focus variant unchanged (E-14/E-15)."""
    subject, shape = split_subject("site:github.com/scipy/scipy latest release")
    assert shape == ""
    assert "site:github.com/scipy/scipy" in subject


def test_a_quoted_title_is_kept_intact():
    subject, shape = split_subject(
        '"Deep Residual Learning for Image Recognition" original paper DOI')
    assert subject.startswith('"Deep Residual Learning')
    assert shape


# ------------------------------------------------- the focus query variant
def test_the_full_question_is_always_sent():
    """The audit: "Keep the full query; do not silently simplify it." """
    for question in ("exoplanet atmosphere spectroscopy replication failures "
                     "primary literature",
                     "What is the capital of Assam?",
                     "nginx reverse proxy websocket version specific known issue"):
        strategies = [v["strategy"] for v in reformulate(question, max_queries=3)]
        assert strategies[0] == "original"
        assert question == reformulate(question, max_queries=3)[0]["text"]


def test_a_research_question_gets_a_focus_variant():
    variants = reformulate(
        "exoplanet atmosphere spectroscopy replication failures primary literature",
        max_queries=3)
    focus = [v for v in variants if v["strategy"] == "focus"]
    assert focus, variants
    assert focus[0]["text"] == "exoplanet atmosphere spectroscopy"


def test_a_two_word_subject_gets_no_focus_variant():
    """A two-word subject collapses onto its head term: "capital assam" is
    a search for Capital One, not for the state."""
    variants = reformulate("What is the capital of Assam?", max_queries=3)
    assert [v for v in variants if v["strategy"] == "focus"] == []


def test_the_site_scope_survives_reformulation():
    variants = reformulate("site:github.com/scipy/scipy latest release", max_queries=3)
    for variant in variants:
        assert "site:github.com/scipy/scipy" in variant["text"]


# ------------------------------------------------- measured against the corpus
def test_the_split_recovers_the_corpus_topics():
    """The corpus records the topic of every templated query, so the split
    can be measured rather than asserted. This is the regression guard on
    the request lexicon."""
    rows = [r for r in _corpus() if r.get("topic")]
    exact = 0
    for row in rows:
        topic = str(row["topic"])
        subject, _shape = split_subject(str(row["query"]))
        if subject.strip().lower() == topic.strip().lower():
            exact += 1
    rate = exact / len(rows)
    assert rate >= 0.85, f"subject split recovered only {rate:.2%} of the topics"


# ------------------------------------------------------------- the metric
def test_coverage_ignores_the_request_shape():
    """A question's request shape is not something a result has to contain
    in order to answer it."""
    question = "Treaty of Yandabo exact date and terms primary source"
    terms = distinguishing_terms(question)
    assert "primary" not in terms
    assert "source" not in terms
    assert "yandabo" in terms


def test_coverage_judges_the_subject_not_the_question():
    """The Wikipedia article "Assam" answers "What is the capital of Assam?"
    even before its snippet says so."""
    verdict = assess_coverage("What is the capital of Assam?", [
        {"title": "Assam", "url": "https://en.wikipedia.org/wiki/Assam",
         "snippet": ""},
    ])
    assert verdict["status"] in ("good", "partial"), verdict


def test_coverage_rejects_generic_filler():
    verdict = assess_coverage(
        "global temperature 2025 WMO Copernicus estimates comparison",
        [{"title": "GLOBLE", "url": "https://www.globe.com/",
          "snippet": "Global business"}])
    assert verdict["status"] == "low", verdict


def test_a_short_keyword_form_is_not_sent():
    """The two-word keyword form of a question collapses onto its head term.
    Measured over real provider results, "capital assam" returns Capital One
    and a dictionary entry — zero on-target results — where the question
    itself returns ten."""
    variants = reformulate("What is the capital of Assam?", max_queries=4)
    assert [v for v in variants if v["strategy"] == "keywords"] == []


def test_a_longer_keyword_form_is_still_sent():
    out = reformulate("how to learn C++ vector syntax", max_queries=4)
    keywords = next(q for q in out if q["strategy"] == "keywords")
    assert "c++" in keywords["text"]


# ------------------------------------------------------------- E-36 scope
def test_the_site_scope_keeps_its_path():
    """A path scope is a repository or a subreddit, not a website."""
    scopes, cleaned = parse_site_scopes("site:github.com/scipy/scipy latest release")
    assert scopes == ["github.com/scipy/scipy"]
    assert cleaned == "latest release"
    assert site_scoped_query("site:github.com/scipy/scipy latest release") == \
        "site:github.com/scipy/scipy latest release"


def test_the_scope_gate_enforces_the_path():
    assert scope_matches("https://github.com/scipy/scipy", "github.com/scipy/scipy")
    assert scope_matches("https://github.com/scipy/scipy/issues/123",
                         "github.com/scipy/scipy")
    assert not scope_matches("https://github.com/pytorch/pytorch",
                              "github.com/scipy/scipy")
    assert not scope_matches("https://github.com/scipy", "github.com/scipy/scipy")


def test_a_path_scoped_query_keeps_its_scope_in_every_variant():
    variants = reformulate("site:github.com/scipy/scipy latest release", max_queries=4)
    assert variants
    for variant in variants:
        assert "site:github.com/scipy/scipy" in variant["text"], variant


# --------------------------------------------------------- temporal qualifiers
def test_a_trailing_year_is_a_temporal_qualifier_not_a_request_shape():
    """A bare year says "as of 2026", not "give me a different kind of page".

    It is separable from the subject, but it must never *shadow* the real
    request shape: "… official dataset download October 2026" asks for a
    dataset, and the year at the end of it used to hide that.
    """
    from iralens.search.queryfocus import match_request_phrase, split_subject

    subject, shape = split_subject("WHO mpox emergency status latest official update October 2026")
    assert subject == "WHO mpox emergency status"
    assert "official" in shape and "2026" in shape

    # A query whose only suffix is the year: the year is separable, and the
    # subject is everything before it.
    subject, shape = split_subject("অসম বন্যা চৰকাৰী তথ্য 2026")
    assert subject == "অসম বন্যা চৰকাৰী তথ্য"
    assert shape == "2026"

    # A bare year is not a known request *phrase*, so it does not split a
    # query on its own: a phrase that covers the whole query has no
    # subject to leave behind.
    assert match_request_phrase("অসম বন্যা চৰকাৰী তথ্য 2026") is None


def test_a_version_qualifies_the_request_rather_than_being_cut_off():
    """A version number belongs with the request it qualifies.

    "Python 3.12 release notes" asks for the release notes of 3.12: the
    version is part of the request, and the product name is the subject. It
    is certainly not a date that may be cut off and dropped.
    """
    from iralens.search.queryfocus import split_subject

    subject, shape = split_subject("Python 3.12 release notes")
    assert subject == "Python"
    assert "3.12" in shape

    subject, shape = split_subject("Attention Is All You Need original paper arXiv 1706.03762")
    assert subject == "Attention Is All You Need"
    assert "1706.03762" in shape
