# -*- coding: utf-8 -*-
"""Mojeek backend: offline parser tests + tertiary default fallback."""
from iralens.search.engines import build_backends
from iralens.search.engines.mojeek import parse_results_page
from iralens.settings import Settings

HTML = """
<html><body><ul class="results-standard">
<li><h2><a href="https://example.gov.in/register-firm">Register a firm</a></h2>
<p class="s">Register a partnership firm: fee and forms.</p></li>
<li><h2><a href="https://example.com/other">Other page</a></h2>
<p>Another snippet.</p></li>
</ul></body></html>
"""


def test_mojeek_parser_extracts_hits():
    page = parse_results_page(HTML)
    assert [h["url"] for h in page.hits] == [
        "https://example.gov.in/register-firm", "https://example.com/other"]
    assert page.hits[0]["title"] == "Register a firm"
    assert "partnership firm" in page.hits[0]["snippet"]


def test_mojeek_registered_as_tertiary_default_fallback():
    assert "mojeek" in build_backends()
    assert Settings().search_engines.index("mojeek") == 2
    assert Settings().search_engines[-1] == "semantic-search"


def test_mojeek_empty_page_is_not_silent():
    page = parse_results_page("<html><body>No results found</body></html>")
    assert page.hits == []
