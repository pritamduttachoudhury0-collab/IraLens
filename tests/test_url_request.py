# -*- coding: utf-8 -*-
"""A query that names a page must be read, not searched for.

The audit's captured 3,000-query run answered all 130 URL-anchored queries
with an unrelated Wikipedia article — "https://docs.python.org/3/" came
back with the article *HTTPS*. These tests pin the routing that fixes it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from iralens.core import IraLens  # noqa: E402
from iralens.model import Artifact  # noqa: E402
from iralens.search.urlrequest import leading_url, read_request  # noqa: E402


# --------------------------------------------------------------- the parsing
@pytest.mark.parametrize("query,expected", [
    ("https://react.dev/learn", "https://react.dev/learn"),
    ("https://react.dev/learn main article text", "https://react.dev/learn"),
    ("https://en.wikipedia.org/wiki/Assam.", "https://en.wikipedia.org/wiki/Assam"),
    ("https://www.nseindia.com/", "https://www.nseindia.com/"),
    ("What is the capital of Assam?", ""),
    ("see https://react.dev/learn for details", ""),
    ("", ""),
])
def test_the_url_is_only_recognised_at_the_head(query, expected):
    assert leading_url(query) == expected


def test_a_url_plus_a_request_is_a_read_request():
    request = read_request("https://react.dev/learn main article text without navigation chrome")
    assert request is not None
    url, shape = request
    assert url == "https://react.dev/learn"
    assert shape == "main article text without navigation chrome"


@pytest.mark.parametrize("query", [
    "What is the capital of Assam?",
    # A question about the page is a question, and the search engines are
    # better at questions than a single read is.
    "https://stackoverflow.com/questions/1 how do I reverse a list?",
    "https://react.dev/learn what is React?",
    # Two pages at once cannot be answered by reading one of them.
    "https://a.example and https://b.example",
    # A URL mentioned in passing is not a request to read it.
    "see https://react.dev/learn for details",
])
def test_a_query_that_is_not_a_read_request_stays_a_search(query):
    assert read_request(query) is None


# ---------------------------------------------------------------- the routing
class _FakeLens(IraLens):
    """A facade whose reader is replaced by a recorded one."""

    def __init__(self, page_text="The React page body.", page_title="React"):
        super().__init__()
        self.reads = []
        self._page_text = page_text
        self._page_title = page_title

    def open(self, url, mode="auto", max_chars=20000, discovered_from=None):
        self.reads.append(url)
        return Artifact(title=self._page_title, url=url, source="web",
                        content=self._page_text, kind="page",
                        retrieval_method="static-reader")


def test_a_named_page_is_read_and_returned():
    lens = _FakeLens()
    artifacts = lens.search("https://react.dev/learn")
    assert lens.reads == ["https://react.dev/learn"]
    assert len(artifacts) == 1
    assert artifacts[0].url == "https://react.dev/learn"
    assert artifacts[0].content == "The React page body."


def test_the_page_is_the_whole_result_set():
    lens = _FakeLens()
    response = lens.search_api("https://react.dev/learn main article text")
    assert lens.reads == ["https://react.dev/learn"]
    assert len(response.results) == 1
    result = response.results[0]
    assert result.url == "https://react.dev/learn"
    assert result.engines == ["read"]
    # The page's own text is the answer, not a snippet of it.
    assert "React page body" in result.snippet
    assert response.outcomes[0].engine == "read"
    assert response.outcomes[0].status == "results"


def test_a_failed_read_falls_back_to_the_search(monkeypatch):
    lens = _FakeLens()

    def _boom(url, mode="auto", max_chars=20000, discovered_from=None):
        raise RuntimeError("the page could not be read")

    monkeypatch.setattr(lens, "open", _boom)
    seen = {}

    class _Engine:
        def search(self, question, filters=None, options=None, context=None):
            seen["question"] = question
            from iralens.search.schema import SearchResponse
            return SearchResponse(
                query=question, results=[], queries=[{"text": question, "strategy": "original"}],
                outcomes=[], fallbacks=[], filters={}, cache={}, dedup_log=[],
                no_results_reason="no_results")

    monkeypatch.setattr(lens, "_search_engine", lambda: _Engine())
    response = lens.search_api("https://react.dev/learn")
    assert seen.get("question") == "https://react.dev/learn"
    assert response.results == []
