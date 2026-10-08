"""Offline parser tests against saved HTML/JSON fixtures (no network)."""

from pathlib import Path

from halfiralens.search.engines.bing import decode_bing_url, parse_results_page as parse_bing
from halfiralens.search.engines.duckduckgo import decode_result_url, parse_results_page as parse_ddg
from halfiralens.search.engines.exa import parse_exa_payload
from halfiralens.search.engines.base import as_html

FIX = Path(__file__).parent / "fixtures" / "search"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_ddg_results_decode_redirects_titles_and_snippets():
    page = parse_ddg(read("ddg_results.html"))
    assert page.block is None
    assert [h["url"] for h in page.hits] == [
        "https://www.example.gov/report?utm_source=x",
        "https://news.example.com/climate-2025",
        "https://blog.example.org/post.pdf",
    ]
    assert page.hits[0]["title"] == "Annual Climate Report"          # whitespace collapsed
    assert page.hits[0]["snippet"] == "The annual report covers 2025 trends."  # nested <b> kept
    assert page.hits[2]["snippet"] == "Plain snippet div."           # lite/div variant


def test_ddg_captcha_is_classified_not_parsed_as_results():
    page = parse_ddg(read("ddg_captcha.html"))
    assert page.hits == [] and page.block == "captcha"


def test_ddg_empty_page_has_no_block_signal():
    page = parse_ddg(read("ddg_empty.html"))
    assert page.hits == [] and page.block is None


def test_ddg_layout_change_detected_by_marker_without_results():
    page = parse_ddg(read("ddg_layout_changed.html"))
    assert page.hits == [] and page.looks_like_serp


def test_decode_result_url_handles_plain_and_wrapped():
    assert decode_result_url("https://x.test/a") == "https://x.test/a"
    assert decode_result_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fy.test%2Fb") == "https://y.test/b"


def test_bing_results_decode_tracking_links_and_snippets():
    page = parse_bing(read("bing_results.html"))
    assert [h["url"] for h in page.hits] == ["https://example.edu/paper", "https://example.edu/paper?utm_medium=cpc"]
    assert page.hits[0]["title"] == "Research paper on solar cells"
    assert page.hits[0]["snippet"] == "A peer reviewed study of solar cell efficiency."


def test_bing_decode_bad_token_returns_href_unchanged():
    href = "https://www.bing.com/ck/a?u=zzz"
    assert decode_bing_url(href) == href


def test_bing_empty_page():
    page = parse_bing(read("bing_empty.html"))
    assert page.hits == [] and page.block is None


def test_exa_payload_parsing_skips_urlless_items_and_reads_dates():
    hits = parse_exa_payload(read("exa_payload.json"), "solar", limit=10)
    assert [h.url for h in hits] == ["https://example.edu/solar", "https://news.example.com/solar"]
    assert hits[0].published_at == "2025-03-14"
    assert hits[1].snippet == "Coverage."
    assert hits[0].engine == "semantic-search"


def test_as_html_unwraps_json_quoted_strings():
    assert as_html('"<html>x</html>"') == "<html>x</html>"
    assert as_html({"result": "<p>y</p>"}) == "<p>y</p>"
    assert as_html(None) == ""
