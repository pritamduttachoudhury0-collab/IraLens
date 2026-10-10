# -*- coding: utf-8 -*-
"""Local HTML -> markdown extraction with the standard library only (D-077).

Local-first reading: pages are fetched directly and converted here, instead
of sending every URL to a remote reader service. The extraction is lossy for
complex/JS-driven pages (no DOM, no CSS layout, no script execution) — see
KNOWN_LIMITATIONS.md — and the browser backend exists for those.

Contract:
  - `html_to_markdown(html)` returns markdown-flavored text: ATX headings,
    paragraphs, lists, blockquotes, fenced code blocks, inline links/code,
    and simple pipe tables. Script/style/noscript/svg/template and
    nav/footer/aside blocks are dropped entirely.
  - `extract_title(html)` returns the <title> text (or the first h1), which
    readers use to label the artifact.
  - Extraction never raises on malformed HTML: HTMLParser is forgiving and
    whatever is parsed is emitted; unreadable input yields "".
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import List, Optional

#: Elements whose entire subtree is noise for page text.
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe",
              "nav", "footer", "aside", "form", "button", "select", "textarea"}
#: Elements that produce block-level boundaries (paragraph breaks).
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "header", "table", "tr",
               "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre",
               "blockquote", "ul", "ol"}
_HEADING = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}


class _MarkdownExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []            # finished lines
        self._buf: List[str] = []           # current inline buffer
        self._skip_depth = 0
        self._heading: Optional[str] = None
        self._heading_prefix = ""
        self._in_pre = False
        self._pre_buf: List[str] = []
        self._in_code = False
        self._in_blockquote = 0
        self._list_stack: List[str] = []    # "ul" | "ol"
        self._ol_counters: List[int] = []
        self._link_href: Optional[str] = None
        self._link_text: List[str] = []
        self._cell_buf: List[str] = []
        self._row: List[str] = []
        self._in_cell = False
        self._title: Optional[str] = None
        self._in_title = False
        self._title_buf: List[str] = []
        self._strong = 0
        self._em = 0

    # ---------------------------------------------------------------- helpers
    def _flush_inline(self) -> None:
        text = "".join(self._buf)
        stripped = " ".join(text.split())
        self._buf = []
        if not stripped:
            return
        line = stripped
        if self._in_blockquote:
            line = "> " + line
        if self._list_stack and self._heading is None:
            indent = "  " * (len(self._list_stack) - 1)
            if self._list_stack[-1] == "ul":
                line = f"{indent}- {stripped}"
            else:
                self._ol_counters[-1] += 1
                line = f"{indent}{self._ol_counters[-1]}. {stripped}"
        self.out.append(line)

    def _flush_block(self) -> None:
        if self._in_pre:
            return
        self._flush_inline()

    # ------------------------------------------------------------------ tags
    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "title":
            self._in_title = True
            self._title_buf = []
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "pre":
            self._flush_block()
            self._in_pre = True
            self._pre_buf = []
            return
        if tag in _HEADING:
            self._flush_block()
            self._heading = tag
            self._heading_prefix = _HEADING[tag]
            self._buf = []
            return
        if tag == "blockquote":
            self._flush_block()
            self._in_blockquote += 1
            return
        if tag in ("ul", "ol"):
            self._flush_block()
            self._list_stack.append(tag)
            if tag == "ol":
                self._ol_counters.append(0)
            return
        if tag == "li":
            self._flush_block()
            return
        if tag == "br":
            self._flush_block()
            return
        if tag in ("td", "th"):
            self._in_cell = True
            self._cell_buf = []
            return
        if tag == "tr":
            self._flush_block()
            self._row = []
            return
        if tag == "a":
            self._link_href = attr.get("href") or ""
            self._link_text = []
            return
        if tag == "code" and not self._in_pre:
            self._in_code = True
            self._buf.append("`")
            return
        if tag in ("strong", "b"):
            self._strong += 1
            self._buf.append("**")
            return
        if tag in ("em", "i"):
            self._em += 1
            self._buf.append("*")
            return
        if tag in _BLOCK_TAGS:
            self._flush_block()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            self._title = " ".join("".join(self._title_buf).split())
            return
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "pre":
            self._in_pre = False
            code = "".join(self._pre_buf).strip("\n")
            if code.strip():
                self.out.append("```")
                self.out.extend(code.splitlines())
                self.out.append("```")
            self._pre_buf = []
            return
        if tag in _HEADING and self._heading == tag:
            text = " ".join("".join(self._buf).split())
            self._buf = []
            self._heading = None
            if text:
                self.out.append(self._heading_prefix + text)
            return
        if tag == "blockquote" and self._in_blockquote:
            self._flush_block()
            self._in_blockquote -= 1
            return
        if tag in ("ul", "ol") and self._list_stack and self._list_stack[-1] == tag:
            self._flush_block()
            self._list_stack.pop()
            if tag == "ol":
                self._ol_counters.pop()
            return
        if tag == "li":
            self._flush_block()
            return
        if tag in ("td", "th") and self._in_cell:
            cell = " ".join("".join(self._cell_buf).split())
            self._cell_buf = []
            self._in_cell = False
            self._row.append(cell)
            return
        if tag == "tr" and self._row:
            self.out.append("| " + " | ".join(self._row) + " |")
            self._row = []
            return
        if tag == "table" and self._row:
            self.out.append("| " + " | ".join(self._row) + " |")
            self._row = []
            return
        if tag == "a":
            text = " ".join("".join(self._link_text).split())
            href = self._link_href or ""
            self._link_href = None
            self._link_text = []
            if href and text and href not in text:
                self._buf.append(f"[{text}]({href})")
            elif text:
                self._buf.append(text)
            return
        if tag == "code" and self._in_code:
            self._in_code = False
            self._buf.append("`")
            return
        if tag in ("strong", "b") and self._strong:
            self._strong -= 1
            self._buf.append("**")
            return
        if tag in ("em", "i") and self._em:
            self._em -= 1
            self._buf.append("*")
            return
        if tag in _BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)
            return
        if self._skip_depth:
            return
        if self._in_pre:
            self._pre_buf.append(data)
            return
        if self._in_cell:
            self._cell_buf.append(data)
            return
        if self._link_href is not None:
            self._link_text.append(data)
            return
        self._buf.append(data)


def html_to_markdown(html: str) -> str:
    """Convert HTML to markdown-flavored text. Lossy by design; never raises."""
    if not html or not html.strip():
        return ""
    parser = _MarkdownExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed input must not break a read
        pass
    parser._flush_block()
    text = "\n".join(line.rstrip() for line in parser.out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def extract_title(html: str) -> str:
    """<title> text, falling back to the first <h1>; '' when neither exists."""
    if not html:
        return ""
    parser = _MarkdownExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""
    if parser._title:
        return parser._title
    for line in parser.out:
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def html_to_text(html: str) -> str:
    """Markdown with link targets dropped — plain text for previews/compare."""
    out: List[str] = []
    for line in html_to_markdown(html).splitlines():
        buf: List[str] = []
        i = 0
        while i < len(line):
            if line[i] == "[":
                close = line.find("](", i)
                end = line.find(")", close + 2) if close != -1 else -1
                if close != -1 and end != -1:
                    buf.append(line[i + 1:close])
                    i = end + 1
                    continue
            buf.append(line[i])
            i += 1
        out.append("".join(buf))
    return "\n".join(out)
