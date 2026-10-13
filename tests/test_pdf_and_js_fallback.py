# -*- coding: utf-8 -*-
"""PDF reading and the JS-shell browser fallback (offline regression tests).

Two root causes fixed here:

* A PDF body (``%PDF-``) used to be decoded as UTF-8 and returned as binary
  noise, so every PDF in the benchmark corpus failed the main-text check even
  though the read "succeeded".
* A client-rendered app shell ("You need to enable JavaScript to run this
  app.") used to be returned as if it were the page text, because the static
  fetch *did* return HTTP 200. In auto mode the browser engine now gets its
  turn automatically; `mode="static"` still never starts it.
"""

import io
import sys
import urllib.error
import zlib
from pathlib import Path

import pytest

from iralens.cache import ResponseCache
from iralens.errors import EngineUnavailableError, ExtractionError, PageUnavailableError
from iralens.extract import looks_like_pdf, pdf_to_text
from iralens.sources import web as web_mod


# ---------------------------------------------------------------------- helpers
def make_pdf(text: bytes = b"Hello IraLens PDF phrase", *, compress: bool = False,
             with_text: bool = True) -> bytes:
    """A hand-built, third-party-style PDF (no pypdf involved in creating it)."""
    content = b"BT /F1 24 Tf 72 700 Td (" + text + b") Tj ET" if with_text else b""
    if compress:
        content = zlib.compress(content)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode()
        + (b" /Filter /FlateDecode" if compress else b"") + b" >>\nstream\n" + content
        + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    startxref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
              + str(startxref).encode() + b"\n%%EOF\n")
    return out.getvalue()


class FakeResponse:
    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def read(self, n=-1):
        return self._buf.read(n if n > 0 else 65536)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_body(monkeypatch, body: bytes) -> None:
    monkeypatch.setattr(web_mod, "safe_urlopen",
                        lambda url, *, timeout=30, headers=None: FakeResponse(body))


def _patch_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(web_mod, "ResponseCache",
                        lambda enabled=True: ResponseCache(tmp_path / "pages"))


class Ctx:
    """Context with an injectable engine (or one that must not be used)."""

    def __init__(self, engine=None):
        self.config = None
        self._engine = engine

    def engine(self):
        if self._engine is None:
            raise AssertionError("the browser engine must not be started here")
        return self._engine


class FakeEngine:
    def __init__(self, text: str, title: str = "Rendered", url: str = "https://example.org/app"):
        self.text = text
        self.title = title
        self.url = url
        self.navigations = 0

    def navigate(self, url, **kwargs):
        self.navigations += 1

    def markdown(self, max_chars=0):
        return self.text

    def current_url_title(self):
        return {"url": self.url, "title": self.title}


# ------------------------------------------------------------------- pdf basics
def test_pdf_magic_detection():
    assert looks_like_pdf(make_pdf())
    assert not looks_like_pdf(b"<html><body>hi</body></html>")
    assert not looks_like_pdf(b"")


@pytest.mark.parametrize("compress", [False, True])
def test_pdf_text_is_extracted(compress):
    text, info = pdf_to_text(make_pdf(compress=compress))
    assert "Hello IraLens PDF phrase" in text
    assert info["pages"] == 1 and info["pages_with_text"] == 1


def test_compressed_and_plain_pdfs_agree():
    plain, _ = pdf_to_text(make_pdf(compress=False))
    packed, _ = pdf_to_text(make_pdf(compress=True))
    assert plain == packed


def test_scanned_pdf_yields_a_typed_error():
    with pytest.raises(ExtractionError) as exc_info:
        pdf_to_text(make_pdf(with_text=False))
    assert exc_info.value.detail == "pdf_no_text"
    assert "scan" in exc_info.value.hint


def test_missing_pdf_dependency_is_typed_and_actionable(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)   # forces ImportError
    with pytest.raises(ExtractionError) as exc_info:
        pdf_to_text(make_pdf())
    assert exc_info.value.detail == "pdf_support_missing"
    assert "pip install" in exc_info.value.hint


def test_scanned_pdf_can_use_bounded_local_ocr(monkeypatch):
    import subprocess
    from iralens import extract as extract_mod

    monkeypatch.setattr(extract_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **kwargs):
        if command[0].endswith("pdftoppm"):
            Path(command[-1] + "-1.png").write_bytes(b"mock-png")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout=b"Recognized local OCR phrase", stderr=b"")

    monkeypatch.setattr(extract_mod.subprocess, "run", fake_run)
    text, info = pdf_to_text(make_pdf(with_text=False), ocr_enabled=True, ocr_max_pages=1)
    assert text == "Recognized local OCR phrase"
    assert info["ocr"] is True and info["pages"] == 1


def test_scanned_pdf_ocr_missing_system_tools_is_typed(monkeypatch):
    from iralens import extract as extract_mod

    monkeypatch.setattr(extract_mod.shutil, "which", lambda _name: None)
    with pytest.raises(ExtractionError) as exc_info:
        pdf_to_text(make_pdf(with_text=False), ocr_enabled=True)
    assert exc_info.value.detail == "pdf_ocr_support_missing"
    assert "tesseract" in exc_info.value.hint


def test_read_direct_returns_pdf_text_not_binary_noise(monkeypatch):
    _patch_body(monkeypatch, make_pdf(compress=True))
    text = web_mod.read_direct("https://example.org/paper.pdf")
    assert "Hello IraLens PDF phrase" in text
    assert "%PDF" not in text
    assert "endstream" not in text


def test_read_url_artifact_for_a_pdf(monkeypatch, tmp_path):
    _patch_body(monkeypatch, make_pdf())
    _patch_cache(monkeypatch, tmp_path)
    artifact = web_mod.WebSource().read_url("https://example.org/paper.pdf",
                                            Ctx(), mode="static", max_chars=5000)
    assert "Hello IraLens PDF phrase" in artifact.content
    assert artifact.retrieval_method == "direct"


def test_parse_failure_is_typed(monkeypatch):
    _patch_body(monkeypatch, b"%PDF-1.7\nthis is not a real pdf body at all\n")
    with pytest.raises(ExtractionError) as exc_info:
        web_mod.read_direct("https://example.org/broken.pdf")
    assert exc_info.value.detail.startswith("pdf_parse_failed")


# ------------------------------------------------------- js-shell auto fallback
def test_js_shell_falls_back_to_the_browser_automatically(monkeypatch, tmp_path):
    shell = "You need to enable JavaScript to run this app."
    monkeypatch.setattr(web_mod, "read_direct", lambda url, **kw: shell)
    _patch_cache(monkeypatch, tmp_path)
    engine = FakeEngine("# Real page\n\n" + "content " * 60)
    artifact = web_mod.WebSource().read_url("https://example.org/app", Ctx(engine),
                                            mode="auto", max_chars=10000)
    assert engine.navigations == 1
    assert artifact.retrieval_method == "browser"
    assert "Real page" in artifact.content
    assert artifact.provenance["fallback"] == {"reason": "js_shell", "result": "browser_text"}


def test_thin_static_read_also_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(web_mod, "read_direct", lambda url, **kw: "Loading…")
    _patch_cache(monkeypatch, tmp_path)
    engine = FakeEngine("A" * 400)
    artifact = web_mod.WebSource().read_url("https://example.org/spa", Ctx(engine), mode="auto")
    assert artifact.retrieval_method == "browser"


def test_plain_page_never_starts_the_engine(monkeypatch, tmp_path):
    body = "# Documentation\n\n" + ("Real page text. " * 40)
    monkeypatch.setattr(web_mod, "read_direct", lambda url, **kw: body)
    _patch_cache(monkeypatch, tmp_path)
    artifact = web_mod.WebSource().read_url("https://example.org/docs", Ctx(), mode="auto")
    assert artifact.retrieval_method == "direct"
    assert "Real page text." in artifact.content


def test_static_mode_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(web_mod, "read_direct",
                        lambda url, **kw: "You need to enable JavaScript to run this app.")
    _patch_cache(monkeypatch, tmp_path)
    artifact = web_mod.WebSource().read_url("https://example.org/app", Ctx(), mode="static")
    assert artifact.retrieval_method == "direct"     # explicit static request is honoured
    assert "enable JavaScript" in artifact.content


def test_js_shell_without_engine_keeps_the_text_it_has(monkeypatch, tmp_path):
    monkeypatch.setattr(web_mod, "read_direct",
                        lambda url, **kw: "Please enable JavaScript to continue.")
    _patch_cache(monkeypatch, tmp_path)

    class NoEngine:
        def navigate(self, url, **kw):
            raise EngineUnavailableError("the browser engine is not installed",
                                         hint="run: iralens install-engine")

    artifact = web_mod.WebSource().read_url("https://example.org/app", Ctx(NoEngine()),
                                            mode="auto")
    assert artifact.retrieval_method == "direct"     # a usable read stays usable
    assert artifact.metadata["static_fallback"] == "js_shell_unavailable"
    assert artifact.provenance["fallback"]["result"] == "browser_unavailable"


def test_browser_text_worse_than_static_keeps_static(monkeypatch, tmp_path):
    shell = "Enable JavaScript. " + "x" * 400     # thin-looking but long enough to be a shell
    monkeypatch.setattr(web_mod, "read_direct", lambda url, **kw: shell)
    _patch_cache(monkeypatch, tmp_path)
    engine = FakeEngine("tiny")
    artifact = web_mod.WebSource().read_url("https://example.org/app", Ctx(engine), mode="auto")
    assert artifact.content == shell
    assert artifact.metadata["fallback_result"] == "kept_static_text"


# ------------------------------------------------------ primary error surfacing
def test_primary_static_error_is_reported_when_the_fallback_also_fails(monkeypatch, tmp_path):
    def direct_fails(url, **kw):
        raise PageUnavailableError("direct fetch could not reach the page", detail="http_404")

    monkeypatch.setattr(web_mod, "read_direct", direct_fails)
    _patch_cache(monkeypatch, tmp_path)

    class NoEngine:
        def navigate(self, url, **kw):
            raise EngineUnavailableError("the browser engine is not installed")

    with pytest.raises(PageUnavailableError) as exc_info:
        web_mod.WebSource().read_url("https://example.org/gone", Ctx(NoEngine()), mode="auto")
    assert exc_info.value.detail == "http_404", "the real reason must not be replaced"
    assert "browser fallback also failed" in exc_info.value.hint


def test_http_error_from_the_browser_is_used_when_static_was_impossible(monkeypatch, tmp_path):
    monkeypatch.setattr(web_mod, "read_direct",
                        lambda url, **kw: (_ for _ in ()).throw(
                            ExtractionError("empty", detail="empty_response")))
    _patch_cache(monkeypatch, tmp_path)

    class BrokenEngine:
        def navigate(self, url, **kw):
            raise urllib.error.URLError("dns failure")

    with pytest.raises(ExtractionError) as exc_info:
        web_mod.WebSource().read_url("https://example.org/x", Ctx(BrokenEngine()), mode="auto")
    assert exc_info.value.detail == "empty_response"
    assert "browser fallback also failed" in exc_info.value.hint
