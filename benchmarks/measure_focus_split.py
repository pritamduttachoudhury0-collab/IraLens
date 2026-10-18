#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the subject/request split against the corpus's own ground truth.

Every query in the frozen corpus except the 50 hand-authored calibration
ones is a `<topic> + <template suffix>` pair, and the corpus records the
topic. That is ground truth for exactly the split the `focus` query variant
depends on: if the splitter does not recover the topic, the focus variant
searches for the wrong thing.

This script reports:

  * how many of the templated queries the splitter gets exactly right;
  * which template suffixes are missed, so the request-marker lexicon can
    be extended with evidence rather than guesses;
  * what the focus variant would actually send for a sample.

    python benchmarks/measure_focus_split.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from iralens.search.queryfocus import split_subject  # noqa: E402


def main() -> int:
    corpus = REPO / "benchmarks" / "audit3000" / "queries.jsonl"
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()
            if line.strip()]

    templated = [r for r in rows if r.get("topic")]
    hand = [r for r in rows if not r.get("topic")]

    exact = 0
    by_suffix: Dict[str, List[bool]] = defaultdict(list)
    misses: List[tuple] = []
    for row in templated:
        topic = str(row["topic"])
        query = str(row["query"])
        subject, _request = split_subject(query)
        ok = subject.strip().lower() == topic.strip().lower()
        exact += ok
        suffix = query[len(topic):].strip() if query.lower().startswith(topic.lower()) else "?"
        by_suffix[suffix].append(ok)
        if not ok and len(misses) < 40:
            misses.append((row["id"], topic, subject, suffix))

    total = len(templated)
    print("=" * 76)
    print("SUBJECT SPLIT — measured against the corpus's own topic field")
    print("=" * 76)
    print(f"templated queries : {total}")
    print(f"hand-authored     : {len(hand)} (no topic recorded — not scored)")
    print(f"topic recovered exactly: {exact}/{total} = {exact / total * 100:.2f}%")
    print()

    wrong = {suf: v for suf, v in by_suffix.items() if not all(v)}
    print(f"template suffixes not fully recovered: {len(wrong)} of {len(by_suffix)}")
    for suffix, oks in sorted(wrong.items(), key=lambda kv: -len(kv[1])):
        got = sum(1 for o in oks if o)
        print(f"  {got:>3}/{len(oks):<3}  {suffix!r}")
    print()
    if misses:
        print("sample misses:")
        for qid, topic, subject, suffix in misses[:12]:
            print(f"  [{qid}] topic={topic!r}")
            print(f"        got  ={subject!r}")
            print(f"        want ={topic!r}   (suffix {suffix!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
