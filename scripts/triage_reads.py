#!/usr/bin/env python3
"""Classify page-read phrase misses from an existing benchmark results.json.

This is an *analysis* tool, not a new benchmark or a replacement for reviewing
HTTP response/reader logs. It never edits the results file, corpus or thresholds.
A missing phrase is not necessarily a product failure (e.g. walled pages), and
the cause is a conservative hypothesis when the record lacks response details.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

CAUSES = ("blocked", "wrong_format", "too_slow", "not_enough_text", "other")


def classify(record: dict[str, Any]) -> tuple[str, str]:
    """Return (cause, evidence) for a phrase miss; unknown evidence -> other."""
    error = str(record.get("error_type") or "").lower()
    detail = str(record.get("error_detail") or "").lower()
    status = str(record.get("status") or "").lower()
    message = f"{error} {detail}"
    if any(s in message for s in (
        "http_401", "http_403", "http_429", "captcha", "antibot", "challenge",
        "rate.limit", "login", "auth", "access denied", "forbidden", "robots",
        "blocked_by_security_policy",
    )):
        return "blocked", "denied/challenge/rate-limit or security policy"
    if any(s in message for s in ("timeout", "timed out", "deadline", "too slow")):
        return "too_slow", "timeout/deadline"
    if any(s in message for s in (
        "pdf_parse_failed", "pdf_support_missing", "unsupported_media", "binary",
        "incorrect format", "content_type", "wrong_format", "http_415",
    )):
        return "wrong_format", "unreadable/unsupported document format"
    if any(s in message for s in (
        "pdf_no_text", "pdf_ocr", "empty_response", "no readable text",
        "js_shell", "no extractable text",
    )):
        return "not_enough_text", "no readable text or missing OCR/rendered text"
    if record.get("requires_js") and record.get("js_fallback_ok") is False:
        return "not_enough_text", "client-rendered page was not read by the browser"
    if error or detail:
        return "other", "typed failure without enough evidence for a specific cause"
    if status == "silent_empty":
        return "not_enough_text", "empty content"
    chars = record.get("content_chars")
    if isinstance(chars, (int, float)) and 0 <= chars < 150:
        return "not_enough_text", f"only {int(chars)} characters returned"
    return "other", "insufficient evidence; inspect the site and source record"


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")[:230]


def report(payload: dict[str, Any], source: str) -> str:
    records = payload.get("extraction", {}).get("urls")
    if not isinstance(records, list):
        raise ValueError("expected benchmark results.json with extraction.urls records")
    if any(not isinstance(r, dict) or not r.get("id") for r in records):
        raise ValueError("each extraction record needs an id")
    missing = [r for r in records if r.get("phrase_ok") is False]
    counts: Counter[str] = Counter()
    lines = [
        "# Page-read phrase misses — triage, not an acceptance result", "",
        f"Input: `{_cell(source)}`. {len(records)} reads recorded; {len(missing)} missed the phrase.",
        "Causes are **hypotheses** from typed errors/text length, not confirmed site diagnoses.",
        "A `phrase_or_walled` page with a typed error may count as a valid refusal in the",
        "frozen benchmark; it is retained here so no phrase miss disappears.", "",
        "| ID | URL | Cause | Evidence | Error/status | Text chars | Expected |",
        "|---|---|---|---|---|---:|---|",
    ]
    for row in missing:
        cause, evidence = classify(row)
        counts[cause] += 1
        lines.append("| " + " | ".join(_cell(v) for v in (
            row["id"], row.get("url"), cause, evidence,
            row.get("error_detail") or row.get("error_type") or row.get("status"),
            row.get("content_chars", "?"), row.get("expect", "?"),
        )) + " |")
    lines += ["", "| Cause | Phrase misses |", "|---|---:|"]
    lines += [f"| {cause} | {counts[cause]} |" for cause in CAUSES]
    lines += ["", "Review each row against the audit report/response before prioritizing a fix.",
              "A typed walled-page refusal is not automatically a failed benchmark read.", ""]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="existing results.json (read-only)")
    parser.add_argument("--out", type=Path, help="new markdown table path (stdout by default)")
    args = parser.parse_args(argv)
    content = report(json.loads(args.results.read_text(encoding="utf-8")), str(args.results))
    if args.out:
        if args.out.resolve() == args.results.resolve():
            parser.error("refusing to overwrite the input results")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(content, encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
