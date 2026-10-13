# -*- coding: utf-8 -*-
"""Local HTML -> markdown extraction with the standard library only (D-077).

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

import io
import re
import shutil
import subprocess
import tempfile
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


# --------------------------------------------------------------------------- pdf
def looks_like_pdf(body: bytes) -> bool:
    """True when the body starts with the PDF header (magic-byte sniff)."""
    return (body or b"")[:1024].lstrip()[:5] == PDF_MAGIC


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
                reader.decrypt("")           # many PDFs are only "owner" encrypted
            except Exception as exc:
                raise ExtractionError(
                    "the PDF is encrypted", hint="decrypt it first, or open it in a browser",
                    detail="pdf_encrypted",
                ) from exc
        pages = list(reader.pages)
        wanted = pages[:max_pages] if max_pages > 0 else pages
        chunks: List[str] = []
        extracted = 0
        for page in wanted:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""                    # one broken page must not lose the document
            if text.strip():
                extracted += 1
            chunks.append(text)
            if sum(len(c) for c in chunks) >= max_chars:
                break
        body = "\n\n".join(c.strip() for c in chunks if c.strip())
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            "the PDF could not be parsed",
            hint="the file may be truncated or corrupt",
            detail=f"pdf_parse_failed: {type(exc).__name__}",
        ) from exc
    if not body.strip():
        if ocr_enabled:
            body = ocr_pdf_to_text(data, max_pages=ocr_max_pages,
                                   max_chars=max_chars, language=ocr_language)
            info = {"pages": len(pages), "pages_with_text": 0,
                    "text_chars": len(body), "ocr": True}
            return body, info
        raise ExtractionError(
            "the PDF contained no extractable text",
            hint="it is probably a scan; enable local OCR and install pdftoppm plus tesseract",
            detail="pdf_no_text",
        )
    info = {"pages": len(pages), "pages_with_text": extracted, "text_chars": len(body)}
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
