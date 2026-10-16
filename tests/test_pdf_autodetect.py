"""Auto OCR on scanned PDFs is local and explicitly reports missing tools."""

import subprocess
from pathlib import Path

import pytest

from iralens import IraLens
from iralens import extract as extract_mod
from iralens.errors import ExtractionError
from iralens.settings import Settings
from iralens.sources import web as web_mod
from tests.test_pdf_and_js_fallback import FakeResponse, make_pdf


def test_read_direct_auto_ocr_when_tools_exist(monkeypatch):
    monkeypatch.setattr(web_mod, "safe_urlopen", lambda url, **kw: FakeResponse(make_pdf(with_text=False)))
    monkeypatch.setattr(extract_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen = []

    def run(command, **kwargs):
        seen.append(command[0])
        if command[0].endswith("pdftoppm"):
            Path(command[-1] + "-1.png").write_bytes(b"mock page")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout=b"Recognized local phrase", stderr=b"")

    monkeypatch.setattr(extract_mod.subprocess, "run", run)
    assert web_mod.read_direct("https://example.org/scan.pdf") == "Recognized local phrase"
    assert seen == ["/usr/bin/pdftoppm", "/usr/bin/tesseract"]


def test_read_direct_scanned_pdf_reports_missing_tools(monkeypatch):
    monkeypatch.setattr(web_mod, "safe_urlopen", lambda url, **kw: FakeResponse(make_pdf(with_text=False)))
    monkeypatch.setattr(extract_mod.shutil, "which", lambda _name: None)
    with pytest.raises(ExtractionError) as err:
        web_mod.read_direct("https://example.org/scan.pdf")
    assert err.value.detail == "pdf_ocr_support_missing"
    assert "pdftoppm" in err.value.message and "tesseract" in err.value.message


def test_ocr_can_be_disabled_by_configuration(monkeypatch):
    from iralens.config import Config

    monkeypatch.setenv("IRALENS_PDF_OCR_ENABLED", "false")
    config = Config(read_only=True)
    assert not Settings.from_config(config).pdf_ocr_enabled
    monkeypatch.setattr(web_mod, "safe_urlopen", lambda url, **kw: FakeResponse(make_pdf(with_text=False)))
    with pytest.raises(ExtractionError) as err:
        web_mod.read_direct("https://example.org/scan.pdf", ocr_enabled=False)
    assert err.value.detail == "pdf_no_text"


def test_doctor_explains_missing_ocr_tools(monkeypatch, isolated_home):
    monkeypatch.setattr(extract_mod.shutil, "which", lambda _name: None)
    with IraLens() as lens:
        pdf = lens.doctor()["pdf"]
    assert pdf["text_pdf_available"] is True
    assert pdf["ocr_enabled"] is True
    assert pdf["ocr_tools_available"] is False
    assert pdf["missing_tools"] == ["pdftoppm", "tesseract"]
    assert "missing" in pdf["message"]
