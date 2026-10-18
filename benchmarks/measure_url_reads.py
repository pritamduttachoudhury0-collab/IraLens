#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the URL-read fix against the audit's own corpus.

130 of the 3,000 corpus queries are a URL, sometimes followed by what the
caller wants from that page. Before the fix they were searched for as
words, so `https://docs.python.org/3/` was answered with the Wikipedia
article *HTTPS*. This script asks the corpus's URL queries again and
compares the coverage verdict of the answer it gets now with the verdict
the captured baseline gave.

    python benchmarks/measure_url_reads.py [--limit N]

Only the URLs the machine can actually reach are measured; the rest are
reported as unreachable, because a network policy is not a defect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CORPUS = REPO / "benchmarks" / "audit3000" / "queries.jsonl"
BASELINE = Path("/tmp/audit-bundle/iralens-audit/records/iralens.jsonl")

_ORDER = {"low": 0, "partial": 1, "good": 2, "unknown": 1}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            # The audit's largest files end in a row that arrives truncated.
            continue
    return rows


def corpus_urls() -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for row in load_jsonl(CORPUS):
        query = (row.get("query") or "").strip()
        if re.match(r"^https?://", query, re.IGNORECASE):
            out.append((str(row.get("id") or "?"), query))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N queries (0 = all)")
    args = parser.parse_args()

    from iralens.core import IraLens
    from iralens.search.coverage import assess_coverage

    urls = corpus_urls()
    if args.limit:
        urls = urls[:args.limit]
    baseline = {row.get("query"): row for row in load_jsonl(BASELINE)}

    lens = IraLens()
    read = 0
    unreachable = 0
    comparable = 0
    improved = 0
    regressed = 0
    examples: List[str] = []

    for query_id, query in urls:
        try:
            response = lens.search_api(query, options={"cache": "bypass"})
        except Exception:
            unreachable += 1
            continue
        if not response.results or response.results[0].engines != ["read"]:
            unreachable += 1
            continue
        read += 1
        now = assess_coverage(query, response.results)
        record = baseline.get(query)
        before = None
        if record:
            before = assess_coverage(query, (record.get("response") or {}).get("results") or [])
        if before is None:
            continue
        comparable += 1
        if _ORDER.get(now["status"], 1) > _ORDER.get(before["status"], 1):
            improved += 1
        elif _ORDER.get(now["status"], 1) < _ORDER.get(before["status"], 1):
            regressed += 1
        if len(examples) < 10:
            top = response.results[0]
            examples.append(
                f"  {query_id} {query[:56]}\n"
                f"      read    -> {top.title[:46]!r} {top.url[:56]}\n"
                f"      coverage {before['status']} -> {now['status']}")

    print("=" * 78)
    print("URL-ANCHORED QUERIES — read instead of searched for")
    print("=" * 78)
    print(f"URL queries in the corpus              : {len(urls)}")
    print(f"read successfully on this machine      : {read}")
    print(f"not reachable from here                : {unreachable}")
    print(f"comparable with the captured baseline  : {comparable}")
    print(f"  coverage improved                    : {improved}")
    print(f"  coverage regressed                   : {regressed}")
    if examples:
        print()
        print("examples:")
        print("\n".join(examples))
    if unreachable:
        print()
        print("Note: an unreachable URL is a network policy, not a defect. A run")
        print("with open egress measures all of them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
