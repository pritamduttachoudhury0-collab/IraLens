# -*- coding: utf-8 -*-
"""F-01: main-content extraction must keep block structure and a clean title.

Every fixture here is a local file under `tests/fixtures/extraction/`, so these
tests run offline and deterministically.

The defect this guards against: `select_main_content()` used to return
*markdown*, which `html_to_markdown()` then fed back to an HTML parser. With
no block tags left to break on, the whole page collapsed into one line, and
the artifact title (taken from the first heading line) became the entire body.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iralens import content_guard
from iralens.extract import extract_title, html_to_markdown, html_to_text, select_main_content

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------- F-01 core
def test_select_main_content_returns_html_not_markdown():
    """The selection/conversion contract: the selector returns HTML, once."""
    selected = select_main_content(load("main_article.html"))
    assert "<article" in selected
    assert "<h1>Coroutines and tasks</h1>" in selected
    # Markdown would have lost the tags; HTML must keep them so the single
    # conversion pass can still see the block boundaries.
    assert "<p>" in selected and "<table>" in selected
    assert not selected.lstrip().startswith("# ")


def test_selected_region_is_converted_exactly_once():
    """Same result whether the region is selected by the caller or internally."""
    page = load("main_article.html")
    region = select_main_content(page)
    assert html_to_markdown(region) == html_to_markdown(page)


def test_main_content_keeps_block_boundaries():
    """Headings, paragraphs and lists stay on separate lines (F-01 acceptance)."""
    md = html_to_markdown(load("main_article.html"))
    lines = [line for line in md.splitlines() if line.strip()]
    assert md.count("\n") > 10, "content was flattened into one line"
    assert "# Coroutines and tasks" in lines
    assert "## Coroutines" in lines
    assert "- First bullet" in lines
    assert "1. Step one" in lines
    assert "2. Step two" in lines


def test_main_content_preserves_links_tables_and_unicode():
    md = html_to_markdown(load("main_article.html"))
    assert "[the asyncio library reference](https://docs.python.org/3/library/asyncio.html)" in md
    assert "| Name | Value |" in md
    assert "| alpha | 1 |" in md
    assert "café naïve" in md
    assert "日本語テキスト" in md
    assert "Ω≈ç√" in md


def test_main_content_excludes_navigation_footer_script_and_style():
    md = html_to_markdown(load("main_article.html"))
    assert "Alpha nav" not in md
    assert "Footer text that must not be selected" not in md
    assert "trackingPixel" not in md
    assert "color: #333" not in md

    md = html_to_markdown(load("script_style.html"))
    assert "must never be extracted" not in md
    assert "Noscript text must be dropped" not in md
    assert "Visible heading" in md


def test_malformed_html_is_tolerated_and_still_structured():
    """Unclosed tags, stray closes and raw ampersands must not break a read."""
    md = html_to_markdown(load("malformed.html"))
    assert "Malformed heading" in md
    assert "ampersand & a **bold** word" in md
    assert "café & naïve text" in md
    assert md.count("\n") >= 2
    # The serializer must not leak unclosed structure into the text.
    assert "</" not in md


def test_page_without_a_qualifying_container_is_left_unchanged():
    page = load("no_main_container.html")
    assert select_main_content(page) == page
    assert "One link" in html_to_markdown(page)
    assert select_main_content("") == ""
    assert select_main_content("   ") == "   "


def test_selection_threshold_behaviour():
    """Just below the prose threshold: no isolation. Just above: isolated."""
    below = load("below_threshold.html")
    assert select_main_content(below) == below

    above = load("above_threshold.html")
    selected = select_main_content(above)
    assert selected != above
    assert "<h1>The real article</h1>" in selected
    assert "Navigation entry number 0" not in selected


# ------------------------------------------------------------------- titles
def test_title_comes_from_html_metadata_and_is_concise():
    title = extract_title(load("main_article.html"))
    assert title == "Coroutines and tasks - Python 3.13.16 documentation"
    assert len(title) < 100


def test_extract_title_falls_back_then_returns_empty():
    assert extract_title(load("empty_title.html")) == ""
    assert extract_title("<html><body><p>no title</p></body></html>") == ""
    assert extract_title("") == ""
    # No <title>: the first <h1> is used.
    assert extract_title("<html><body><h1>  Fallback   Head </h1></body></html>") == "Fallback Head"


# ------------------------------------------------------- html_to_text / helpers
def test_html_to_text_drops_link_targets_but_keeps_labels():
    text = html_to_text('<main><article><p>Go <a href="https://x.test">here</a> now.</p></article></main>')
    assert "here" in text and "https://x.test" not in text


# ------------------------------------------------- end-to-end read pipeline
class _StaticContext:
    """Minimal context for a static read: asking for the engine is a failure."""

    class _Config:
        def get(self, key, default=None):
            return default

    def __init__(self) -> None:
        self.config = self._Config()

    def engine(self):
        raise AssertionError("a static read must not start the browser engine")


@pytest.fixture()
def read_page(monkeypatch, tmp_path):
    """Read a fixture file through the real read pipeline, with no network."""
    from iralens import sources
    from iralens.cache import ResponseCache

    web_mod = sources.web
    pending: dict = {}

    class FakeResponse:
        """One-shot body: the reader loops until `read()` returns empty."""

        def __init__(self, body: bytes) -> None:
            self._body = body
            self._done = False

        def read(self, _n=-1):
            if self._done:
                return b""
            self._done = True
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def headers(self):
            return {"Content-Type": "text/html; charset=utf-8"}

        @property
        def status(self):
            return 200

        @property
        def url(self):
            return "https://docs.example.org/page"

    monkeypatch.setattr(web_mod, "safe_urlopen",
                        lambda url, *a, **k: FakeResponse(pending["body"]))
    monkeypatch.setattr(web_mod, "ResponseCache",
                        lambda enabled=True: ResponseCache(tmp_path / "pages"))

    def read(name: str, max_chars: int = 20000):
        pending["body"] = load(name).encode("utf-8")
        return web_mod.WebSource().read_url(
            "https://docs.example.org/page", _StaticContext(),
            mode="static", max_chars=max_chars)

    return read


def test_read_pipeline_returns_structured_content_and_a_bounded_title(read_page):
    """End-to-end: the read path keeps structure, a clean title, and the budget."""
    art = read_page("main_article.html", max_chars=1200)

    # 1. Structure survived the extraction.
    assert art.content.count("\n") > 10
    assert "# Coroutines and tasks" in art.content
    assert "## Coroutines" in art.content

    # 2. The title is page metadata, not a body excerpt.
    assert art.title == "Coroutines and tasks - Python 3.13.16 documentation"
    assert len(art.title) < 100
    assert art.metadata["title_source"] == "html"

    # 3. The content budget is enforced on content, independently of the title.
    assert len(art.content) <= 1200
    assert art.metadata["truncated"] is True
    assert art.metadata["total_chars"] > 1200
    assert "content_truncated" in art.warnings


def test_read_pipeline_content_never_exceeds_the_budget(read_page):
    """The hard character budget holds at, below and above the boundary."""
    full = read_page("main_article.html", max_chars=100000)
    total = full.metadata["total_chars"]
    assert total > 1000

    for budget in (1, 10, 40, 200, 1000, 5000, 100000):
        art = read_page("main_article.html", max_chars=budget)
        assert len(art.content) <= budget, f"budget {budget} exceeded"
        assert art.metadata["truncated"] is (budget < total)
        if art.metadata["truncated"]:
            assert art.metadata["total_chars"] == total


def test_read_pipeline_title_is_bounded_when_metadata_is_huge(read_page):
    """A ~400-character <title> still produces a concise artifact title."""
    from iralens import sources

    art = read_page("long_title.html")
    assert len(art.title) <= sources.web._MAX_TITLE_CHARS
    assert art.metadata["title_truncated"] is True


def test_read_pipeline_falls_back_to_the_url_without_any_heading(read_page):
    """No <title> and no <h1>: the URL labels the artifact, never body text."""
    art = read_page("empty_title.html")
    assert art.metadata["title_source"] == "url"
    assert art.title == "https://docs.example.org/page"
    # The h2 is content, not a title: it must not be promoted to one.
    assert "Only a subheading here" in art.content


# ------------------------------------------------- F-07 benign-content regression
def test_documentation_fixture_is_not_flagged_as_a_tool_directive():
    """The exact phrasing from the Python asyncio page (F-01/F-07 fixture)."""
    md = html_to_markdown(load("main_article.html"))
    assert "run function func in a separate thread" in md.lower()
    flags = content_guard.scan(md)
    assert "prompt_injection:tool_directive" not in flags
    assert flags == []
