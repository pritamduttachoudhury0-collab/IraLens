# -*- coding: utf-8 -*-
"""Local HTML -> markdown extraction with the standard library only.

Local-first reading: pages are fetched directly and converted here, instead
of sending every URL to a remote reader service. The extraction is lossy for
complex/JS-driven pages (no DOM, no CSS layout, no script execution) — see
KNOWN_LIMITATIONS.md — and the browser backend exists for those.

PDF bodies are detected by their magic bytes and converted with `pypdf` when
it is installed (``pip install pypdf``, or the ``[pdf]`` extra). Without it the
read fails with a typed `extraction_failed`/`pdf_support_missing` error that
names the fix, instead of returning binary noise as if it were page text.

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

import importlib.util
import io
import re
import shutil
import subprocess
import tempfile
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import ExtractionError

#: PDF magic: the header is `%PDF-<version>` within the first bytes of the file.
PDF_MAGIC = b"%PDF-"

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


# ------------------------------------------------------------ main content
#: Elements that *are* the main content of a page.
_MAIN_TAGS = ("main", "article")
#: id/class fragments that mark a main-content container.
_MAIN_MARKERS = (
    "mw-parser-output", "mw-content-text", "post-content", "post-body",
    "article-content", "article-body", "entry-content", "markdown-body",
    "rich-text", "richtext", "prose", "main-content", "content-body",
    "content-main", "page-content", "story-body",
)
#: A candidate must hold at least this share of the visible text to be
#: preferred over the whole document.
_MAIN_MIN_SHARE = 0.5
#: Below this many visible characters a candidate is not worth isolating.
_MAIN_MIN_CHARS = 200
#: Link density of the text *outside* a candidate. Above this, what the
#: candidate leaves behind is a menu, and the candidate is the content.
_MAIN_OUTSIDE_LINK_RATIO = 0.5
#: A candidate must hold at least this share of the page's *prose*.
_MAIN_MIN_PROSE_SHARE = 0.5


def _content_key(tag: str, attrs: List[Any]) -> Optional[str]:
    """The identity of a main-content candidate, or None."""
    attr = dict(attrs or [])
    if tag in _MAIN_TAGS:
        return f"tag:{tag}"
    role = str(attr.get("role") or "").strip().lower()
    if role == "main":
        return "role:main"
    ident = str(attr.get("id") or "").strip().lower()
    for marker in _MAIN_MARKERS:
        if ident == marker or ident.startswith(marker + "-") or ident.endswith("-" + marker):
            return f"id:{ident}"
    classes = str(attr.get("class") or "").strip().lower().split()
    for name in classes:
        for marker in _MAIN_MARKERS:
            if name == marker or name.endswith("-" + marker):
                return f"class:{name}"
    return None


#: Tags that end a text segment. A segment is the unit the prose test
#: looks at, so it must not straddle two blocks.
_SEGMENT_BOUNDARY_TAGS = frozenset("""
p div li td th tr dd dt section article main header footer nav aside ul ol dl
h1 h2 h3 h4 h5 h6 blockquote pre table tbody thead tfoot figure figcaption
form fieldset details summary address hr br
""".split())

#: A text segment is prose when it has at least this many words...
_PROSE_MIN_WORDS = 5
#: ...and either ends like a sentence or runs on at least this long.
_PROSE_SENTENCE_WORDS = 10


def _is_prose_segment(segment: str) -> bool:
    """Whether a text segment reads as prose rather than as a label.

    Menus, table headers and captions are short and unpunctuated; prose
    is either a sentence or a long run of words.
    """
    words = segment.split()
    if len(words) < _PROSE_MIN_WORDS:
        return False
    if len(words) >= _PROSE_SENTENCE_WORDS:
        return True
    return bool(re.search(r"[.!?][\"')]?\s*$", segment.strip()))


class _ContentFinder(HTMLParser):
    """Measures the visible text of every main-content candidate.

    For each candidate it records how much text its subtree holds, how much
    of that text is prose, and how much sits inside links, so the caller
    can tell "this element *is* the content" from "this element is a menu".
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.total_text = 0
        self.total_link = 0
        self.total_prose = 0
        self.candidates: List[list] = []   # [key, ordinal, subtree_len, link_len, prose_len]
        self._skip = 0
        self._link_depth = 0
        self._stack: List[tuple] = []       # (tag, key, ordinal)
        self._open: List[list] = []         # open candidates
        self._counters: Dict[str, int] = {}
        self._buffer = ""

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._flush_buffer()
            self._skip += 1
            return
        if tag == "a":
            self._link_depth += 1
        if tag in _SEGMENT_BOUNDARY_TAGS:
            self._flush_buffer()
        key = _content_key(tag, attrs)
        if key is None:
            self._stack.append((tag, None, 0))
            return
        ordinal = self._counters.get(key, 0)
        self._counters[key] = ordinal + 1
        self._stack.append((tag, key, ordinal))
        self._open.append([key, ordinal, 0, 0, 0])

    def handle_startendtag(self, tag, attrs):
        key = _content_key(tag, attrs)
        if key is None:
            return
        ordinal = self._counters.get(key, 0)
        self._counters[key] = ordinal + 1
        self.candidates.append([key, ordinal, 0, 0, 0])

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._flush_buffer()
            if self._skip:
                self._skip -= 1
            return
        if tag == "a" and self._link_depth:
            self._link_depth -= 1
        if tag in _SEGMENT_BOUNDARY_TAGS:
            self._flush_buffer()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                _, key, ordinal = self._stack.pop(index)
                if key is not None:
                    for position in range(len(self._open) - 1, -1, -1):
                        entry = self._open[position]
                        if entry[0] == key and entry[1] == ordinal:
                            self._open.pop(position)
                            self.candidates.append(entry)
                            break
                break

    def handle_data(self, data):
        if self._skip:
            return
        self._buffer += data
        while "\n" in self._buffer:
            segment, self._buffer = self._buffer.split("\n", 1)
            self._count_segment(segment)

    def close(self):  # noqa: D102 - flush the last segment
        super().close()
        if self._buffer:
            self._count_segment(self._buffer)
            self._buffer = ""

    def _flush_buffer(self) -> None:
        """Close the current text segment (a block boundary was reached)."""
        if self._buffer:
            self._count_segment(self._buffer)
            self._buffer = ""

    def _count_segment(self, segment: str) -> None:
        length = len(segment.strip())
        if not length:
            return
        self.total_text += length
        in_link = 1 if self._link_depth else 0
        if in_link:
            self.total_link += length
        prose = length if _is_prose_segment(segment) else 0
        if prose:
            self.total_prose += prose
        for entry in self._open:
            entry[2] += length
            if in_link:
                entry[3] += length
            entry[4] += prose


class _ScopedExtractor(_MarkdownExtractor):
    """A markdown extractor that only collects the chosen candidate's text.

    The candidate is identified by the same key and ordinal the finder
    assigned it, so both passes agree on which element is meant without
    having to reproduce byte offsets.
    """

    def __init__(self, want_key: str, want_ordinal: int) -> None:
        super().__init__()
        self._want_key = want_key
        self._want_ordinal = want_ordinal
        self._counters: Dict[str, int] = {}
        self._active = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        key = _content_key(tag, attrs)
        if key is not None:
            ordinal = self._counters.get(key, 0)
            self._counters[key] = ordinal + 1
            if self._active:
                self._depth += 1
                return
            if key == self._want_key and ordinal == self._want_ordinal:
                self._active = True
                self._depth = 0
                return
        if self._active:
            self._depth += 1
            super().handle_starttag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        if self._active:
            super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag):
        if not self._active:
            return
        if self._depth > 0:
            self._depth -= 1
            super().handle_endtag(tag)
            return
        # depth == 0: this closes the candidate itself.
        self._active = False
        super().handle_endtag(tag)

    def handle_data(self, data):
        if self._active:
            super().handle_data(data)


def select_main_content(html: str) -> str:
    """The HTML of the page's main content region, or the page unchanged.

    Blacklisting ``<nav>``/``<footer>`` is not enough: a page whose
    navigation is a plain ``<ul>`` — a 111-language list, say — still spends
    the whole character budget on the menu. When a main-content container
    can be identified and it holds the bulk of the visible text, only that
    region is kept.
    """
    if not html or not html.strip():
        return html
    finder = _ContentFinder()
    try:
        finder.feed(html)
        finder.close()
    except Exception:
        return html
    # A candidate must hold at least half of the page's prose. That is what
    # separates "the article" from "the menu next to the article": a
    # navigation list has no prose in it at all.
    needed_prose = finder.total_prose * _MAIN_MIN_PROSE_SHARE
    best = None
    for entry in finder.candidates:
        key, ordinal, length = entry[0], entry[1], entry[2]
        prose = entry[4]
        if length < _MAIN_MIN_CHARS:
            continue
        if prose < needed_prose:
            continue
        # Among the candidates that hold the prose, take the *tightest* one:
        # the smallest region that still contains the article. That is how
        # `<main>` (which also contains the page's language menu) loses to
        # the article container inside it.
        if best is None:
            best = (key, ordinal, length, prose)
            continue
        if prose > best[3]:
            best = (key, ordinal, length, prose)
        elif prose == best[3] and length < best[2]:
            best = (key, ordinal, length, prose)
    if best is None:
        return html
    scoped = _ScopedExtractor(best[0], best[1])
    try:
        scoped.feed(html)
        scoped.close()
    except Exception:
        return html
    text = "\n".join(line.rstrip() for line in scoped.out)
    if len(text.strip()) < _MAIN_MIN_CHARS:
        return html
    return text


def html_to_markdown(html: str, *, main_content: bool = True) -> str:
    """Convert HTML to markdown-flavored text. Lossy by design; never raises.

    With ``main_content`` (the default) the page's main-content region is
    isolated first, so a navigation-heavy page does not spend the whole
    character budget on its menu.
    """
    if not html or not html.strip():
        return ""
    if main_content:
        html = select_main_content(html)
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


# --------------------------------------------------------------------------- pdf
def pdf_capabilities(*, ocr_enabled: bool = True) -> Dict[str, Any]:
    """Non-invasive availability check for doctor; do not claim OCR was run."""
    text_pdf = importlib.util.find_spec("pypdf") is not None
    missing = [name for name in ("pdftoppm", "tesseract") if not shutil.which(name)]
    available = not missing
    if not text_pdf:
        message = "text PDF support missing: install pypdf"
    elif not ocr_enabled:
        message = "text PDFs ready; local scanned-PDF OCR disabled by configuration"
    elif missing:
        message = ("text PDFs ready; local scanned-PDF OCR unavailable: missing "
                   + ", ".join(missing) + " (install poppler-utils and tesseract-ocr)")
    else:
        message = "text PDFs ready; local scanned-PDF OCR enabled (not benchmarked)"
    return {"status": "ok" if text_pdf and (not ocr_enabled or available) else "warn",
            "text_pdf_available": text_pdf, "ocr_enabled": ocr_enabled,
            "ocr_tools_available": available, "missing_tools": missing,
            "message": message}


def looks_like_pdf(body: bytes) -> bool:
    """Find a PDF header within the first 1024 bytes, even after a UTF-8 BOM.

    Some valid PDFs contain leading transport junk; HTML containing an example
    `%PDF-` string must not accidentally be parsed as a real PDF.
    """
    prefix = (body or b"")[:1024]
    offset = prefix.find(PDF_MAGIC)
    return offset >= 0 and b"<" not in prefix[:offset]


def _normalize_pdf_text(text: str) -> str:
    """Repair copy/paste artifacts that hide otherwise present PDF phrases."""
    text = unicodedata.normalize("NFKC", text.replace("\u00ad", "").replace("\x00", ""))
    return re.sub(r"(?<=\w)-[ \t]*\n(?=\w)", "", text).strip()


def _poppler_pdf_text(data: bytes, max_pages: int, max_chars: int) -> str:
    """Bounded local text-PDF fallback; absent Poppler is not an error."""
    executable = shutil.which("pdftotext")
    if not executable or len(data) > 20 * 1024 * 1024:
        return ""
    page_limit = min(200, max(1, max_pages or 200))
    with tempfile.TemporaryDirectory(prefix="iralens-pdf-text-") as temp_dir:
        source = Path(temp_dir) / "document.pdf"
        output = Path(temp_dir) / "text.txt"
        source.write_bytes(data)
        try:
            with output.open("wb") as sink:
                proc = subprocess.run(
                    [executable, "-f", "1", "-l", str(page_limit), "-enc", "UTF-8",
                     "-layout", str(source), "-"],
                    stdout=sink, stderr=subprocess.DEVNULL, timeout=45, check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if proc.returncode != 0:
            return ""
        with output.open("rb") as handle:
            return _normalize_pdf_text(handle.read(max_chars * 4).decode("utf-8", "replace"))[:max_chars]


def pdf_to_text(data: bytes, *, max_pages: int = 0,
                max_chars: int = 2_000_000, ocr_enabled: bool = False,
                ocr_max_pages: int = 5, ocr_language: str = "eng") -> Tuple[str, Dict[str, Any]]:
    """Extract readable text from a PDF body.

    Returns `(text, info)` where info carries page counts for the artifact
    metadata. Raises a typed ExtractionError when the PDF cannot be read, when
    it has no extractable text (scanned images), or when the optional PDF
    dependency is missing — never binary noise. Optional local OCR requires
    `pdftoppm` and `tesseract`; it is opt-in via `ocr_enabled` and sends no
    document bytes to a remote service.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - exercised via sys.modules stub
        raise ExtractionError(
            "PDF support requires the 'pypdf' package",
            hint="install it with: pip install 'iralens[pdf]' (or pip install pypdf)",
            detail="pdf_support_missing",
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")  # some PDFs permit empty user passwords
            except Exception as exc:
                raise ExtractionError(
                    "the PDF is encrypted", hint="decrypt it first, or open it in a browser",
                    detail="pdf_encrypted",
                ) from exc
            # pypdf returns 0/NOT_DECRYPTED for a *wrong* password without
            # raising. Do not misreport the resulting empty pages as scans.
            if not unlocked:
                raise ExtractionError(
                    "the PDF requires a password", hint="decrypt it first",
                    detail="pdf_encrypted",
                )
        pages = list(reader.pages)
        wanted = pages[:max_pages] if max_pages > 0 else pages
        chunks: List[str] = []
        extracted = 0
        total_chars = 0
        for page in wanted:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""                    # one broken page must not lose the document
            text = _normalize_pdf_text(text)
            if text:
                extracted += 1
            chunks.append(text)
            total_chars += len(text)
            if total_chars >= max_chars:
                break
        body = "\n\n".join(c for c in chunks if c)[:max_chars]
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            "the PDF could not be parsed",
            hint="the file may be truncated or corrupt",
            detail=f"pdf_parse_failed: {type(exc).__name__}",
        ) from exc
    from_poppler = False
    if len(body) < 80:
        # pypdf can miss a text layer (font mappings/layout); try another
        # *local* text extractor before classifying this as an image-only scan.
        alternate = _poppler_pdf_text(data, max_pages, max_chars)
        if len(alternate) > len(body):
            body = alternate
            from_poppler = True
    if not body.strip():
        if ocr_enabled:
            body = ocr_pdf_to_text(data, max_pages=ocr_max_pages,
                                   max_chars=max_chars, language=ocr_language)
            ocr_info: Dict[str, Any] = {"pages": len(pages), "pages_with_text": 0,
                                        "text_chars": len(body), "ocr": True}
            return body, ocr_info
        raise ExtractionError(
            "the PDF contained no extractable text",
            hint="it is probably a scan; enable local OCR and install pdftoppm plus tesseract",
            detail="pdf_no_text",
        )
    info: Dict[str, Any] = {"pages": len(pages), "pages_with_text": extracted,
                            "text_chars": len(body)}
    if from_poppler:
        info["text_extractor"] = "pdftotext"
    return body, info


def ocr_pdf_to_text(data: bytes, *, max_pages: int = 5,
                    max_chars: int = 2_000_000, language: str = "eng") -> str:
    """OCR a small number of scanned PDF pages locally through Poppler/Tesseract.

    This optional path is deliberately subprocess-only: no shell is involved,
    executable paths are resolved before use, the PDF is staged in a private
    temporary directory, output size/pages are bounded, and every process has
    a deadline. Tesseract language values are restricted to simple locale ids.
    """
    renderer = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not renderer or not tesseract:
        raise ExtractionError(
            "local PDF OCR needs pdftoppm and tesseract",
            hint="install poppler-utils and tesseract-ocr, then set IRALENS_PDF_OCR_ENABLED=true",
            detail="pdf_ocr_support_missing",
        )
    if len(data) > 20 * 1024 * 1024:
        raise ExtractionError("PDF is too large for local OCR", detail="pdf_ocr_input_too_large")
    if not re.fullmatch(r"[A-Za-z0-9_+-]{1,64}", language or ""):
        raise ExtractionError("invalid OCR language identifier", detail="pdf_ocr_bad_language")
    page_limit = min(20, max(1, int(max_pages)))
    with tempfile.TemporaryDirectory(prefix="iralens-ocr-") as temp_dir:
        root = Path(temp_dir)
        source = root / "document.pdf"
        prefix = root / "page"
        source.write_bytes(data)
        command = [renderer, "-f", "1", "-l", str(page_limit), "-r", "150",
                   "-scale-to", "2000", "-png", str(source), str(prefix)]
        try:
            rendered = subprocess.run(command, capture_output=True, timeout=90, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError("local PDF page rendering timed out",
                                  detail="pdf_ocr_render_timeout") from exc
        except OSError as exc:
            raise ExtractionError("local PDF page rendering failed",
                                  detail=f"pdf_ocr_render_failed:{type(exc).__name__}") from exc
        if rendered.returncode != 0:
            raise ExtractionError("local PDF page rendering failed",
                                  detail=f"pdf_ocr_render_exit:{rendered.returncode}")
        images = sorted(root.glob("page-*.png"))[:page_limit]
        if not images:
            raise ExtractionError("local PDF OCR produced no page images",
                                  detail="pdf_ocr_no_pages")
        chunks: List[str] = []
        for image in images:
            try:
                recognized = subprocess.run(
                    [tesseract, str(image), "stdout", "-l", language, "--psm", "3"],
                    capture_output=True, timeout=60, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ExtractionError("local PDF OCR timed out",
                                      detail="pdf_ocr_page_timeout") from exc
            except OSError as exc:
                raise ExtractionError("local PDF OCR failed",
                                      detail=f"pdf_ocr_failed:{type(exc).__name__}") from exc
            if recognized.returncode != 0:
                raise ExtractionError("local PDF OCR failed",
                                      detail=f"pdf_ocr_exit:{recognized.returncode}")
            text = recognized.stdout.decode("utf-8", errors="replace").strip()
            if text:
                chunks.append(text)
            if sum(map(len, chunks)) >= max_chars:
                break
        result = "\n\n".join(chunks)[:max_chars].strip()
        if not result:
            raise ExtractionError("OCR found no readable text in the scanned PDF",
                                  detail="pdf_ocr_no_text")
        return result
