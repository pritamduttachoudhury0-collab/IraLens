#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calibrate the coverage metric against the audit's own human labels.

The audit reviewed 45 of the 3,000 queries by hand and judged their result
sets "unrelated or generic filler". That is the only human relevance
labelling that exists. This script measures how well a coverage metric
reproduces that judgement, and how many of the other 2,955 queries it
objects to.

It also reports a *contamination* warning that matters: the review was
performed on the pre-fix baseline, where every Wikipedia hit had an empty
snippet (E-03). A reviewer could not see what a Wikipedia page said, so
for a Wikipedia-only result set the label is a judgement made blind. The
script reports how many of the 45 are affected.

    python benchmarks/calibrate_coverage.py --baseline <iralens.jsonl>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from iralens.search.coverage import (  # noqa: E402
    assess_coverage, distinguishing_terms,
)

DEFAULT_BASELINE = Path("/tmp/audit-bundle/iralens-audit/records/iralens.jsonl")
DEFAULT_FAILURES = Path("/tmp/audit-bundle/iralens-audit/records/failures.jsonl")
DEFAULT_EVALUATIONS = Path("/tmp/audit-bundle/iralens-audit/records/evaluations.jsonl")


def _load(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            # The audit's largest JSONL files have no closing fence in the
            # part they start in, so the final row can arrive truncated.
            continue
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--failures", type=Path, default=DEFAULT_FAILURES)
    ap.add_argument("--evaluations", type=Path, default=DEFAULT_EVALUATIONS)
    args = ap.parse_args()

    records = _load(args.baseline)
    if not records:
        print(f"baseline not found: {args.baseline}")
        return 2

    confirmed: Set[str] = set()
    for e in _load(args.evaluations):
        judgment = (e.get("dimensions") or {}).get("relevance", {}).get("judgment")
        if judgment == "defective_unrelated_or_generic_filler":
            confirmed.add(str(e.get("query_id")))

    # How many of the 45 were reviewed with at least one *snippet-bearing*
    # result? A result set whose every snippet is empty was reviewed blind.
    blind: Set[str] = set()
    for f in _load(args.failures):
        if f.get("issue_id") != "S-RL":
            continue
        qid = str(f.get("query_id"))
        observed = f.get("observed")
        try:
            items = json.loads(observed) if isinstance(observed, str) else (observed or [])
        except Exception:
            items = []
        if not isinstance(items, list):
            items = []
        if items and not any(str(i.get("snippet") or "").strip() for i in items):
            blind.add(qid)

    # How many of the 45 were reviewed with a Wikipedia hit whose title
    # matches the question's subject? Those pages carried an empty snippet
    # (E-03), so the reviewer could not see that they answered the question.
    wikipedia_matches: Set[str] = set()
    for f in _load(args.failures):
        if f.get("issue_id") != "S-RL":
            continue
        qid = str(f.get("query_id"))
        observed = f.get("observed")
        items = []
        if isinstance(observed, str):
            start = observed.find("[")
            if start >= 0:
                try:
                    items = json.loads(observed[start:])
                except Exception:
                    items = []
        elif isinstance(observed, list):
            items = observed
        for item in items or []:
            url = str(item.get("url") or "")
            if "wikipedia.org" not in url:
                continue
            title = str(item.get("title") or "").lower()
            if not title:
                continue
            rec = next((r for r in records if str(r.get("query_id")) == qid), None)
            if rec is None:
                continue
            question = rec.get("query") or (rec.get("response") or {}).get("query") or ""
            for term in distinguishing_terms(question):
                if term in title:
                    wikipedia_matches.add(qid)
                    break

    flags = Counter()
    flagged: Set[str] = set()
    for rec in records:
        resp = rec.get("response") or {}
        results = resp.get("results") or []
        q = rec.get("query") or resp.get("query") or ""
        verdict = assess_coverage(q, results)
        flags[verdict.get("status")] += 1
        if verdict.get("status") == "low":
            flagged.add(str(rec.get("query_id")))

    tp = len(flagged & confirmed)
    fp = len(flagged - confirmed)
    fn = len(confirmed - flagged)

    print("=" * 74)
    print("COVERAGE METRIC — calibrated against the audit's 45 human labels")
    print("=" * 74)
    print(f"verdicts over the 3,000-query baseline: {dict(flags)}")
    print()
    print(f"human-labelled defective        : {len(confirmed)}")
    print(f"  of which a Wikipedia hit whose TITLE matches the question's subject: "
          f"{len(wikipedia_matches)}")
    print("    (those pages carried an empty snippet before E-03, so the reviewer")
    print("     could not see that they answered the question: the label is a")
    print("     judgement made blind and cannot be used as ground truth)")
    print(f"metric flags                    : {len(flagged)}")
    print(f"  true positives (flagged & human)   : {tp}")
    print(f"  false positives (flagged, not human): {fp}")
    print(f"  false negatives (human, not flagged): {fn}")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print(f"precision {precision:.3f}   recall {recall:.3f}")
    print()
    print("Reading: the metric must catch the queries a human called filler")
    print("(recall) without objecting to thousands of queries the audit never")
    print("reviewed (precision). It is a proxy either way: the audit reviewed")
    print(f"{len(confirmed)} of 3,000 and called the rest 'unresolved'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
