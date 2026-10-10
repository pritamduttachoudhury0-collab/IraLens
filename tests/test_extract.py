# -*- coding: utf-8 -*-
"""Local HTML -> markdown extraction (D-077). Pure functions, offline."""

from iralens.extract import extract_title, html_to_markdown, html_to_text


def test_headings_and_paragraphs():
    html = "<html><body><h1>Title</h1><p>One.</p><h2>Sub</h2><p>Two.</p></body></html>"
    md = html_to_markdown(html)
    assert md.startswith("# Title")
    assert "## Sub" in md
    assert "One." in md and "Two." in md


def test_lists_bulleted_and_numbered():
    html = "<ul><li>alpha</li><li>beta</li></ul><ol><li>first</li><li>second</li></ol>"
    md = html_to_markdown(html)
    assert "- alpha" in md and "- beta" in md
    assert "1. first" in md and "2. second" in md


def test_links_kept_as_markdown():
    html = '<p>See <a href="https://example.org/x">the docs</a> now.</p>'
    md = html_to_markdown(html)
    assert "[the docs](https://example.org/x)" in md


def test_pre_and_inline_code():
    html = "<pre><code>line1\nline2</code></pre><p>use <code>pip</code></p>"
    md = html_to_markdown(html)
    assert "```\nline1\nline2\n```" in md
    assert "`pip`" in md


def test_script_style_nav_dropped():
    html = ("<html><head><style>p{}</style><script>alert(1)</script></head>"
            "<body><nav>menu</nav><footer>foot</footer><p>keep</p>"
            "<div><p>also</p></div></body></html>")
    md = html_to_markdown(html)
    assert "keep" in md and "also" in md
    assert "alert" not in md and "menu" not in md and "foot" not in md


def test_blockquote_and_bold_italic():
    html = "<blockquote><p>quoted</p></blockquote><p><strong>bold</strong> and <em>it</em></p>"
    md = html_to_markdown(html)
    assert "> quoted" in md
    assert "**bold**" in md and "*it*" in md


def test_table_rows_use_pipes():
    html = "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
    md = html_to_markdown(html)
    assert "| a | b |" in md
    assert "| 1 | 2 |" in md


def test_extract_title_prefers_title_then_h1():
    assert extract_title("<html><head><title> Page  Name </title></head><body></body></html>") == "Page Name"
    assert extract_title("<html><body><h1>Heading</h1></body></html>") == "Heading"
    assert extract_title("") == ""


def test_malformed_html_never_raises():
    junk = "<p><b><i>unclosed <div><span>text"
    out = html_to_markdown(junk)
    assert "text" in out


def test_empty_input_is_empty_string():
    assert html_to_markdown("") == ""
    assert html_to_markdown("   ") == ""


def test_plain_text_passes_through_readably():
    md = html_to_markdown("just words\nmore words")
    assert "just words" in md and "more words" in md


def test_html_to_text_drops_link_targets():
    text = html_to_text('<p>Read <a href="https://x.test/a">this</a>.</p>')
    assert "this" in text
    assert "https://x.test/a" not in text
