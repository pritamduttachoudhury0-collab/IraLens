#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Does the request-shape preference change the answer, and for the better?

The request shape says which *kind* of source would answer a question —
the official documentation, the primary source, the government record. This
script re-ranks the audit's own captured 3,000-query result set twice, once
with the preference and once without, through the real engine, and reports:

  * how many queries' top result changes;
  * whether the coverage verdict of the new top result is better.

    python benchmarks/measure_shape_ranking.py
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
from iralens.search import ranking as ranking_mod  # noqa: E402

BASELINE = Path("/tmp/audit-bundle/iralens-audit/records/iralens.jsonl")


def load() -> List[Dict[str, Any]]:
    rows = []
    if not BASELINE.is_file():
        return rows
    for line in BASELINE.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def main() -> int:
    records = load()
    if not records:
        print(f"baseline not found: {BASELINE}")
        return 2

    if "--boost" in sys.argv:
        ranking_mod._REQUEST_KIND_BONUS = float(sys.argv[sys.argv.index("--boost") + 1])

    from iralens.search.intent import classify_query
    from iralens.search.ranking import combine, relevance
    from iralens.settings import Settings

    settings = Settings()
    weights = {"relevance": settings.weight_relevance,
               "authority": settings.weight_authority,
               "freshness": settings.weight_freshness,
               "agreement": settings.weight_agreement}

    changed = 0
    improved = 0
    worsened = 0
    same_verdict = 0
    examples: List[tuple] = []
    kind_queries = 0

    real_bonus = ranking_mod._request_kind_bonus

    def rerank(question: str, results: List[Dict[str, Any]], with_shape: bool):
        # `relevance()` applies the preference itself, so the "without" run
        # has to switch it off rather than subtract it afterwards.
        ranking_mod._request_kind_bonus = (
            real_bonus if with_shape else (lambda u, ti, q: 0.0))
        intent = classify_query(question)
        best = None
        for result in results:
            ranks = result.get("engine_ranks") or {}
            best_rank = min(ranks.values()) if ranks else 1
            title = str(result.get("title") or "")
            snippet = str(result.get("snippet") or "")
            url = str(result.get("url") or "")
            rel = relevance(best_rank, title, snippet, question, url=url,
                            answer_type=intent.answer_type)
            breakdown = result.get("score_breakdown") or {}
            factors = {
                "relevance": rel,
                "authority": breakdown.get("authority", 0.0),
                "freshness": breakdown.get("freshness", 0.0),
                "agreement": breakdown.get("agreement", 0.0),
            }
            score = combine(factors, weights)
            if best is None or score > best[0]:
                best = (score, result)
        ranking_mod._request_kind_bonus = real_bonus
        return best[1] if best else None

    for rec in records:
        resp = rec.get("response") or {}
        results = resp.get("results") or []
        question = rec.get("query") or resp.get("query") or ""
        if not results:
            continue
        if not ranking_mod.request_kinds(question):
            continue
        kind_queries += 1
        without = rerank(question, results, with_shape=False)
        with_shape = rerank(question, results, with_shape=True)
        if without is None or with_shape is None:
            continue
        if str(without.get("url")) == str(with_shape.get("url")):
            continue
        changed += 1
        before = assess_coverage(question, [without])
        after = assess_coverage(question, [with_shape])
        order = {"low": 0, "partial": 1, "good": 2, "unknown": 1}
        if order.get(after["status"], 1) > order.get(before["status"], 1):
            improved += 1
        elif order.get(after["status"], 1) < order.get(before["status"], 1):
            worsened += 1
        else:
            same_verdict += 1
        if len(examples) < 12:
            examples.append((question, str(without.get("title"))[:42],
                             str(with_shape.get("title"))[:42],
                             before["status"], after["status"]))

    if "--sweep" in sys.argv:
        print("=" * 78)
        print("REQUEST-SHAPE BOOST — swept against the captured 3,000-query set")
        print("=" * 78)
        for boost in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00):
            ranking_mod._REQUEST_KIND_BONUS = boost
            changed = improved = worsened = same = 0
            for rec in records:
                resp = rec.get("response") or {}
                results = resp.get("results") or []
                question = rec.get("query") or resp.get("query") or ""
                if not results or not ranking_mod.request_kinds(question):
                    continue
                without = rerank(question, results, with_shape=False)
                on = rerank(question, results, with_shape=True)
                if without is None or on is None:
                    continue
                if str(without.get("url")) == str(on.get("url")):
                    continue
                changed += 1
                b = assess_coverage(question, [without])["status"]
                a = assess_coverage(question, [on])["status"]
                order = {"low": 0, "partial": 1, "good": 2, "unknown": 1}
                if order.get(a, 1) > order.get(b, 1):
                    improved += 1
                elif order.get(a, 1) < order.get(b, 1):
                    worsened += 1
                else:
                    same += 1
            print(f"  bonus {boost:>5}: top-1 changed {changed:>4}   "
                  f"better {improved:>3}   worse {worsened:>3}   same {same:>3}   "
                  f"net {improved - worsened:+d}")
        return 0

    print("=" * 78)
    print("REQUEST-SHAPE PREFERENCE — effect on the top result")
    print("=" * 78)
    print(f"queries that ask for a particular kind of source : {kind_queries}")
    print(f"  of those, the top result changes in             : {changed}")
    if changed:
        print(f"    the new top result is better (coverage)     : {improved}")
        print(f"    the new top result is worse (coverage)      : {worsened}")
        print(f"    the coverage verdict is unchanged           : {same_verdict}")
        print()
        for question, before, after, bs, as_ in examples:
            print(f"  {question[:70]}")
            print(f"      before: {before!r}  [{bs}]")
            print(f"      after : {after!r}  [{as_}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
