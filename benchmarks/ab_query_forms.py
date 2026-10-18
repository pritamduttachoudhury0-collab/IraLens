#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B: does the `focus` query form return better results than the original?

The measurement is over **real provider result pages**, captured live and
read by hand (`benchmarks/fixtures/provider_capture.json`). For each capture
it reports:

  * the on-target rate — the share of results a human judged to address the
    question;
  * the coverage verdict IraLens's own metric reaches on those results, and
    whether that verdict agrees with the human judgement.

The second half is the important one: a metric that calls a page full of
filler "good" is worse than no metric, because the research run then
believes it has answered the question.

    python benchmarks/ab_query_forms.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from iralens.search.coverage import assess_coverage  # noqa: E402
from iralens.search.querytext import parse_site_operator, term_set  # noqa: E402

#: The metric as it was before E-37: a flat fraction of *all* the question's
#: content terms matched by the best result's title, snippet and URL.
_GENERIC = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how i in into is it its of on or should so than that the their them there these
they this to was we were what when where which who why will with would you your
""".split())


def legacy_coverage(question, results):
    """The pre-E-37 coverage verdict, for comparison."""
    _domains, cleaned = parse_site_operator(question or "")
    terms = [w for w in term_set(cleaned) if w not in _GENERIC]
    if not terms:
        return "unknown"
    best = 0.0
    for result in results:
        text = " ".join(str(result.get(k) or "") for k in ("title", "snippet", "url")).lower()
        matched = sum(1 for term in terms if term in text)
        best = max(best, matched / len(terms))
    if best >= 0.6:
        return "good"
    if best >= 0.3:
        return "partial"
    return "low"

FIXTURE = REPO / "benchmarks" / "fixtures" / "provider_capture.json"


def main() -> int:
    data: Dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    captures = data["captures"]

    by_query: Dict[str, List[dict]] = {}
    for cap in captures:
        by_query.setdefault(cap["query"], []).append(cap)

    print("=" * 78)
    print("QUERY-FORM A/B — real provider results, hand-labelled")
    print("=" * 78)

    totals = {"original": [0, 0], "focus": [0, 0], "keywords": [0, 0]}
    agree = {"original": [0, 0], "focus": [0, 0], "keywords": [0, 0]}
    legacy_agree = [0, 0]
    new_agree = [0, 0]

    for query, caps in by_query.items():
        print(f"\n{query}")
        for cap in caps:
            results = cap["results"]
            on = sum(1 for r in results if r.get("on_target"))
            total = len(results)
            verdict = assess_coverage(query, results)
            # The metric agrees with the human when it says the results are
            # usable and most of them are on target, or it says they are not
            # usable and few of them are.
            usable = verdict["status"] in ("good", "partial", "unknown")
            human_usable = on / total >= 0.5 if total else False
            totals.setdefault(cap["form"], [0, 0])
            totals[cap["form"]][0] += on
            totals[cap["form"]][1] += total
            agree.setdefault(cap["form"], [0, 0])
            agree[cap["form"]][0] += int(usable == human_usable)
            legacy = legacy_coverage(query, results)
            legacy_usable = legacy in ("good", "partial", "unknown")
            legacy_agree[1] += 1
            legacy_agree[0] += int(legacy_usable == human_usable)
            new_agree[1] += 1
            new_agree[0] += int(usable == human_usable)
            agree[cap["form"]][1] += 1
            print(f"  {cap['engine']:<11} {cap['form']:<9} "
                  f"on-target {on:>2}/{total:<2} = {on / total * 100:5.1f}%   "
                  f"coverage={verdict['status']:<8} (was {legacy:<8}) "
                  f"{verdict['note'][:44]}")

    print()
    print("=" * 78)
    print("BY QUERY FORM")
    print("=" * 78)
    for form, (on, total) in totals.items():
        if not total:
            continue
        good = agree[form][0]
        checks = agree[form][1]
        print(f"  {form:<9} on-target {on:>3}/{total:<3} = {on / total * 100:5.1f}%   "
              f"metric agrees with the human label in {good}/{checks} captures")

    print()
    print("=" * 78)
    print("THE METRIC, OLD AND NEW, AGAINST THE SAME HUMAN LABELS")
    print("=" * 78)
    print(f"  pre-E-37 metric agrees with the human label in "
          f"{legacy_agree[0]}/{legacy_agree[1]} captures")
    print(f"  post-E-37 metric agrees with the human label in "
          f"{new_agree[0]}/{new_agree[1]} captures")
    print()
    print("Reading: the original form is the baseline. The focus form is the")
    print("subject of the question sent on its own, in addition to the full")
    print("question. A higher on-target rate for the focus form means the")
    print("degraded-network case (one engine captcha'd, the other loose) now")
    print("has a better query to fall back on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
