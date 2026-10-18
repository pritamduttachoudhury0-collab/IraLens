#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the audit's evidence bundle from the uploaded audit documents.

The audit ships its evidence as embedded-file blocks inside the Markdown
documents. Each block is delimited by a machine-readable header
(`<!-- IRALENS_EMBEDDED_FILE {...} -->`) and a machine-readable footer
(`<!-- IRALENS_EMBEDDED_FILE_END -->`), with the file's own declared size
and SHA-256 in the header. Two of the largest files (the per-query JSONL
records) have no closing fence in the part they start in, which is why a
fence-matching extractor loses them.

This script uses the footer markers instead, and verifies every recovered
file against its declared hash. It is the recovery step for the audit
bundle; it is not part of the product.

    python benchmarks/extract_audit_bundle.py
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

UPLOADS = Path("/tmp/arena-coding-uploads")
OUT = Path("/tmp/audit-bundle/iralens-audit")
PARTS = ["weaknesses_part1.md", "weaknesses_part2.md",
         "weaknesses_part3.md", "weaknesses_part4.md"]

HEADER = re.compile(r"^<!-- IRALENS_EMBEDDED_FILE (\{.*\}) -->$")
FOOTER = "<!-- IRALENS_EMBEDDED_FILE_END -->"
TITLE = re.compile(r"^## Embedded file: `(.+)`$")


def main() -> int:
    if not UPLOADS.is_dir():
        print(f"uploads not found: {UPLOADS}")
        return 2
    text = ""
    for name in PARTS:
        path = UPLOADS / name
        if path.is_file():
            text += path.read_text(encoding="utf-8", errors="replace") + "\n"

    lines = text.split("\n")
    recovered = 0
    checked = 0
    mismatched: list = []
    index = 0
    total = len(lines)
    while index < total:
        match = TITLE.match(lines[index])
        if not match:
            index += 1
            continue
        rel = match.group(1)
        # find the machine-readable header, then the opening fence
        cursor = index
        meta = None
        while cursor < total:
            header = HEADER.match(lines[cursor].strip())
            if header:
                meta = json.loads(header.group(1))
                break
            cursor += 1
        if meta is None:
            index += 1
            continue
        cursor += 1
        while cursor < total and not lines[cursor].startswith("```"):
            cursor += 1
        if cursor >= total:
            index += 1
            continue
        cursor += 1  # past the opening fence
        body: list = []
        while cursor < total and lines[cursor].strip() != FOOTER:
            body.append(lines[cursor])
            cursor += 1
        if cursor >= total:
            index += 1
            continue
        # the closing fence sits just before the footer
        if body and body[-1].strip() == "```":
            body.pop()
        # The split already carries the file's own trailing newline; adding
        # another makes every file one byte too long.
        payload = "\n".join(body)
        target = OUT / rel.replace("iralens-audit/", "", 1) if rel.startswith(
            "iralens-audit/") else OUT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        recovered += 1
        declared = meta.get("sha256")
        if declared:
            checked += 1
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != declared:
                mismatched.append((rel, declared, digest))
        index = cursor + 1

    print(f"recovered {recovered} files; {checked} hash-checked")
    if mismatched:
        print("MISMATCHES:")
        for rel, want, got in mismatched:
            print(f"  {rel}\n    declared {want}\n    actual   {got}")
    else:
        print("every hash-checked file matches its declared SHA-256")
    for name in ("records/iralens.jsonl", "records/failures.jsonl",
                 "records/evaluations.jsonl"):
        path = OUT / name
        if path.is_file():
            count = sum(1 for line in path.read_text(encoding="utf-8",
                                                     errors="replace").splitlines()
                        if line.strip())
            print(f"  {name}: {count} rows")
        else:
            print(f"  {name}: MISSING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
