#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What is actually inside the unresolved queries?

The projection reports 1,363 queries the coverage metric still objects to.
They are reported as `unresolved` — an upstream finding, not a code failure —
which is honest but not very informative. This script looks inside them:

  * which corpus family and which request template they cluster on;
  * what their top results actually are;
  * whether the cluster is a *systematic* pattern that the code can fix, or
    a spread of genuinely hard questions.

    python benchmarks/analyse_unresolved.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from iralens.search.coverage import assess_coverage  # noqa: E402

BASELINE = Path("/tmp/audit-bundle/iralens-audit/records/iralens.jsonl")
CORPUS = REPO / "benchmarks" / "audit3000" / "queries.jsonl"


def main() -> int:
    if not BASELINE.is_file():
        print(f"baseline not found: {BASELINE}")
        return 2
    records = []
    for line in BASELINE.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    corpus = {}
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            corpus[str(row["id"])] = row

    unresolved: List[Dict[str, Any]] = []
    for rec in records:
        resp = rec.get("response") or {}
        results = resp.get("results") or []
        question = rec.get("query") or resp.get("query") or ""
        verdict = assess_coverage(question, results)
        if verdict["status"] != "low":
            continue
        row = corpus.get(str(rec.get("query_id")), {})
        topic = str(row.get("topic") or "")
        suffix = ""
        if topic and question.lower().startswith(topic.lower()):
            suffix = question[len(topic):].strip()
        unresolved.append({
            "query_id": str(rec.get("query_id")),
            "question": question,
            "family": str(row.get("family") or "?"),
            "suffix": suffix or "(no separable suffix)",
            "distinguishing": verdict["total"],
            "matched": verdict["matched"],
            "top": [str((r.get("title") or r.get("url") or ""))[:60]
                    for r in results[:3]],
        })

    total = len(records)
    print("=" * 78)
    print("INSIDE THE UNRESOLVED QUERIES")
    print("=" * 78)
    print(f"queries the coverage metric calls low : {len(unresolved)} of {total}")
    print()
    print("by corpus family:")
    for fam, count in Counter(u["family"] for u in unresolved).most_common():
        print(f"  {count:>5}  {fam}")
    print()
    print("by request template (top 20):")
    for suffix, count in Counter(u["suffix"] for u in unresolved).most_common(20):
        print(f"  {count:>5}  {suffix!r}")
    print()
    print("how much of the question's vocabulary the best result matched:")
    hist = Counter(f"{u['matched']}/{u['distinguishing']}" for u in unresolved)
    for key, count in hist.most_common(12):
        print(f"  {count:>5}  {key}")
    print()
    print("sample of what the top results actually are:")
    for item in unresolved[::max(1, len(unresolved) // 12)][:12]:
        print(f"  [{item['query_id']}] {item['question'][:66]}")
        for title in item["top"]:
            print(f"        - {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
