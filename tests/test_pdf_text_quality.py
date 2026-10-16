"""PDF text-layer fixes; all documents/subprocesses are local test fakes."""

import io
import subprocess

import pytest
from pypdf import PdfReader, PdfWriter

from iralens import extract as pdf
from iralens.errors import ExtractionError
from iralens.sources import web
from tests.test_pdf_and_js_fallback import FakeResponse, make_pdf


def test_pdf_magic_allows_bom_but_not_html_code_example():
    assert pdf.looks_like_pdf(b"\xef\xbb\xbf " + make_pdf())
    assert not pdf.looks_like_pdf(b"<html>file header %PDF-1.4</html>")


def test_text_pdf_repairs_ligatures_and_line_break_hyphens():
    assert pdf._normalize_pdf_text("This \ufb01le is hy-\nphenated\x00") == "This file is hyphenated"


def test_pdf_with_wrong_password_is_not_misclassified_as_scan():
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(make_pdf())).pages[0])
    writer.encrypt("s3cr3t")
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(ExtractionError) as exc:
        pdf.pdf_to_text(stream.getvalue(), ocr_enabled=True)
    assert exc.value.detail == "pdf_encrypted"


def test_sparse_text_pdf_prefers_local_poppler_when_richer(monkeypatch):
    original_which = pdf.shutil.which
    monkeypatch.setattr(pdf.shutil, "which",
                        lambda name: "/usr/bin/pdftotext" if name == "pdftotext" else original_which(name))
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        kwargs["stdout"].write(b"Longer useful text layer recovered by local Poppler")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(pdf.subprocess, "run", run)
    text, info = pdf.pdf_to_text(make_pdf(text=b"x"), ocr_enabled=False)
    assert "recovered by local Poppler" in text
    assert info["text_extractor"] == "pdftotext"
    assert seen[0][0].endswith("pdftotext") and "-layout" in seen[0]


def test_pdf_has_higher_but_bounded_download_cap(monkeypatch):
    class WithHeaders(FakeResponse):
        def __init__(self, body, mime):
            super().__init__(body)
            self.headers = {"Content-Type": mime}

    stub = b"%PDF-1.4\n" + b"x" * 1500
    monkeypatch.setattr(web, "safe_urlopen",
                        lambda url, **kwargs: WithHeaders(stub, "application/pdf"))
    monkeypatch.setattr(web, "pdf_to_text", lambda data, **kwargs: ("Local PDF phrase", {}))
    assert web.read_direct("https://example.org/paper", max_bytes=1024,
                           pdf_max_bytes=2048) == "Local PDF phrase"
    monkeypatch.setattr(web, "safe_urlopen",
                        lambda url, **kwargs: WithHeaders(stub, "text/html"))
    with pytest.raises(ExtractionError) as exc:
        web.read_direct("https://example.org/article", max_bytes=1024,
                        pdf_max_bytes=2048)
    assert exc.value.detail == "response_too_large"
