# -*- coding: utf-8 -*-
"""A question that asks for a kind of source should get that kind of source.

"official documentation", "primary literature" and "government guideline"
are not decoration: they say which kind of page would answer the question.
These tests pin the preference that follows from that.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from iralens.search.ranking import (  # noqa: E402
    _REQUEST_KIND_BONUS,
    _request_kind_bonus,
    relevance,
    request_kinds,
)


# ------------------------------------------------------------- the detection
@pytest.mark.parametrize("question,expected", [
    ("What is the capital of Assam?", set()),
    ("FastAPI background task exceptions official documentation", {"documentation", "official"}),
    ("Attention Is All You Need replication failures primary literature", {"primary"}),
    ("measles vaccination schedules Indian government guideline", {"official"}),
    ("NOAA global temperature anomaly official dataset download", {"dataset", "official"}),
])
def test_the_request_shape_says_which_kind_of_source_is_wanted(question, expected):
    assert set(request_kinds(question)) == expected


def test_a_question_without_a_request_shape_asks_for_nothing_in_particular():
    assert request_kinds("Brahmaputra River length") == frozenset()


# -------------------------------------------------------------- the effect
def test_a_page_of_the_wanted_kind_is_preferred():
    question = "FastAPI background task exceptions official documentation"
    docs = "https://fastapi.tiangolo.com/tutorial/background-tasks/"
    blog = "https://example.com/blog/fastapi-background-tasks"
    assert _request_kind_bonus(docs, "Background Tasks - FastAPI", question) == _REQUEST_KIND_BONUS
    assert _request_kind_bonus(blog, "All about FastAPI background tasks", question) == 0.0
    assert relevance(1, "Background Tasks - FastAPI", "FastAPI background tasks",
                     question, url=docs) > relevance(
        1, "All about FastAPI background tasks", "FastAPI background tasks",
        question, url=blog)


def test_a_homepage_is_not_the_page_that_answers():
    """A homepage is the entry point to the right kind, not the source."""
    question = "ISRO launch schedule latest official update"
    assert _request_kind_bonus("https://www.isro.gov.in/", "ISRO", question) == 0.0
    assert _request_kind_bonus("https://www.isro.gov.in/launches", "Launches | ISRO", question) > 0.0


def test_the_preference_is_bounded():
    """The kind of a page cannot outrank a page that simply answers."""
    question = "FastAPI background task exceptions official documentation"
    # A page of the wrong kind that answers the question outright, and a
    # page of the right kind that barely mentions it.
    answering = relevance(1, "FastAPI background task exceptions",
                          "FastAPI background tasks raise exceptions in the task",
                          question, url="https://example.com/answer")
    almost_empty = relevance(1, "Reference", "Reference", question,
                              url="https://fastapi.tiangolo.com/reference/")
    assert answering > almost_empty


def test_the_marker_matching_is_on_whole_words():
    """'database' is not a dataset, and 'released' is not a release."""
    question = "India GDP official dataset download"
    assert _request_kind_bonus("https://example.com/database", "Database Systems", question) == 0.0
    assert _request_kind_bonus("https://data.gov.in/gdp.csv", "Gross Domestic Product data", question) > 0.0
