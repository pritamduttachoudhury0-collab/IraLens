#!/usr/bin/env python3
"""Offline extraction measurement for the frozen 100-page set.

This evaluates the existing local HTML extractor against marked expected
answer spans. It is a deliberately synthetic, fixture-only first measurement;
it is not a live-page extraction benchmark. Run:

    .venv/bin/python benchmark/holdout/evaluate_extraction.py
"""
from __future__ import annotations
import json
from pathlib import Path
from iralens.extract import html_to_text

PAGES = Path(__file__).with_name("extraction_pages.jsonl")


def main() -> None:
    rows = [json.loads(line) for line in PAGES.read_text(encoding="utf-8").splitlines() if line]
    passed = 0
    missing = []
    for row in rows:
        extracted = html_to_text(row["html"])
        ok = row["expected_span"] in extracted
        passed += int(ok)
        if not ok:
            missing.append(row["page_id"])
    total = len(rows)
    print(f"fixture extraction expected-span presence: {passed}/{total} = {passed/total:.3f}")
    print(f"misses: {','.join(missing) if missing else 'none'}")
    print("NOTE: synthetic local HTML fixtures only; live 100-page extraction quality NOT MEASURED.")


if __name__ == "__main__":
    main()
