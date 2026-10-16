"""Offline triage; never edits the frozen corpus or audit output."""

import json

import pytest

from scripts.triage_reads import classify, main, report


@pytest.mark.parametrize("detail,kind", [
    ("http_403", "blocked"),
    ("antibot_challenge", "blocked"),
    ("slow_response_deadline", "too_slow"),
    ("pdf_parse_failed: PdfReadError", "wrong_format"),
    ("pdf_no_text", "not_enough_text"),
    ("http_404", "other"),
])
def test_classify_error_categories(detail, kind):
    assert classify({"error_detail": detail, "content_chars": 0})[0] == kind


def test_classify_sparse_js_and_long_wrong_phrase():
    assert classify({"requires_js": True, "js_fallback_ok": False,
                     "content_chars": 500})[0] == "not_enough_text"
    assert classify({"content_chars": 2000, "status": "wrong_text"})[0] == "other"


def test_report_retains_typed_walled_refusal_and_counts_each_phrase_miss():
    payload = {"extraction": {"urls": [
        {"id": "one", "phrase_ok": True},
        {"id": "two", "url": "https://example.org/a", "phrase_ok": False,
         "error_detail": "http_403", "expect": "phrase_or_walled"},
        {"id": "three", "url": "https://example.org/p.pdf", "phrase_ok": False,
         "error_detail": "pdf_parse_failed", "expect": "phrase"},
    ]}}
    text = report(payload, "my results.json")
    assert "3 reads recorded; 2 missed" in text
    assert "| blocked | 1 |" in text
    assert "| wrong_format | 1 |" in text
    assert "| not_enough_text | 0 |" in text
    assert "phrase_or_walled" in text
    assert "| one |" not in text


def test_cli_keeps_input_unchanged(tmp_path):
    source = tmp_path / "results.json"
    data = json.dumps({"extraction": {"urls": [{"id": "a", "phrase_ok": False,
                                                  "status": "silent_empty", "content_chars": 0}]}})
    source.write_text(data)
    out = tmp_path / "triage.md"
    assert main([str(source), "--out", str(out)]) == 0
    assert source.read_text() == data
    assert "not_enough_text" in out.read_text()
    with pytest.raises(SystemExit):
        main([str(source), "--out", str(source)])


def test_missing_schema_fails_instead_of_reporting_zero():
    with pytest.raises(ValueError, match="extraction.urls"):
        report({}, "none")
