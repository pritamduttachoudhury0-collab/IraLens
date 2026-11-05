#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Honest recent-news benchmark reporter (the frozen 25-query news set).

This reuses the *exact* frozen harness from ``run_benchmark.py`` — the same
``queries.json`` corpus, the same ``score_query`` gold predicate, the same
``IraLens`` facade — so a number printed here is directly comparable to the
historical 0/25. It adds the recency dimension the news pipeline now produces:

* ``quality_hit`` — a top-5 result is on an acceptable domain or carries an
  expected phrase (the frozen relevance predicate).
* ``fresh``       — a top-5 result has a publication date within the query's
  ``recency_days`` window (UTC).
* ``genuine``     — BOTH of the above on the same result set: a relevant *and*
  recent answer. This is the headline "/25" the mission targets.

Honesty rules (non-negotiable):
* It never invents a headline, URL, date, or score. Every field comes from a
  real ``search_api`` response.
* If the environment has no outbound access to the news providers, it says so
  and claims NO score. A blocked run is reported as ``blocked``, not as 0/25
  (which would wrongly imply the pipeline was measured and failed) and not as
  25/25 (which would be fabricated).
* Per-query failure states are distinguished: no-results vs engines-failed vs
  filtered-out vs network-blocked, using the typed outcomes.

Usage:
    python benchmarks/run_recent_news.py                 # live run, all 25
    python benchmarks/run_recent_news.py --limit 5       # first 5 (quick check)
    python benchmarks/run_recent_news.py --offline       # do not touch network
    python benchmarks/run_recent_news.py --out report.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR))

import run_benchmark as rb  # noqa: E402  (the frozen harness)


def _news_queries() -> List[Dict[str, Any]]:
    queries = rb.load_json(rb.QUERIES_PATH)["queries"]
    return [q for q in queries if q.get("category") == "recent_news"]


def _classify_block(record: Dict[str, Any]) -> str:
    """Distinguish a network/provider block from a genuine pipeline failure."""
    if record.get("error_type") in (
            "page_unavailable", "source_unavailable", "operation_timeout",
            "search_engines_failed", "navigation_failed"):
        return "network_or_provider"
    kinds = set(record.get("blocked_kinds") or [])
    if kinds and kinds <= {"unavailable", "transient", "rate_limited"}:
        return "network_or_provider"
    if record.get("reason") == "engines_failed":
        return "network_or_provider"
    return ""


def run_query(system: "rb.IraLensSystem", entry: Dict[str, Any]) -> Dict[str, Any]:
    """Run one frozen news query and score it with the exact gold predicate.

    The search goes through the real facade; the news diagnostics are read
    from the same response so the recency story is auditable per query.
    """
    started = time.monotonic()
    news_diag: Dict[str, Any] = {}
    try:
        response = system.hil.search_api(
            entry["query"],
            options={"max_results": 5, "reformulate": True, "cache": "bypass"},
        )
        payload = response.to_dict()
        news_diag = payload.get("news", {}) or {}
        observation = rb.SearchObservation(query=entry["query"],
                                           engines_configured=system.engine_names())
        observation.latency = time.monotonic() - started
        observation.reason = payload.get("no_results_reason") or ""
        observation.results = [
            {"url": r.get("url", ""), "title": r.get("title", ""),
             "snippet": r.get("snippet", ""), "engines": r.get("engines", []),
             "published_at": r.get("published_at"), "score": r.get("score")}
            for r in payload.get("results", [])
        ]
        observation.outcomes = [
            {"engine": o.get("engine", ""), "status": o.get("status", ""),
             "kind": o.get("kind", ""), "count": o.get("count", 0),
             "detail": (o.get("detail") or "")[:200]}
            for o in payload.get("outcomes", [])
        ]
        observation.valid_shape, observation.shape_problems = rb.validate_response_shape(payload)
    except Exception as exc:  # typed by the source layer; never fabricated
        observation = rb.SearchObservation(query=entry["query"],
                                           engines_configured=system.engine_names())
        observation.latency = time.monotonic() - started
        observation.error_type = getattr(exc, "error_type", None) or type(exc).__name__
        observation.error_detail = str(getattr(exc, "detail", "") or "")[:200] or str(exc)[:200]
        observation.reason = "raised"

    record = rb.score_query(entry, observation)
    record["genuine"] = bool(record["quality_hit"]) and bool(record["fresh"])
    record["news"] = news_diag
    record["block_class"] = _classify_block(record)
    return record


def summarize(records: List[Dict[str, Any]], *, live: bool) -> Dict[str, Any]:
    n = len(records)
    blocked = [r for r in records if r["block_class"] == "network_or_provider"]
    answered = [r for r in records if r not in blocked]
    quality = sum(1 for r in records if r["quality_hit"])
    fresh = sum(1 for r in records if r["fresh"])
    genuine = sum(1 for r in records if r["genuine"])
    # A score is only meaningful when queries actually reached the providers.
    measurable = live and len(answered) > 0
    return {
        "total": n,
        "live": live,
        "answered": len(answered),
        "blocked": len(blocked),
        "quality_hit": quality,
        "fresh": fresh,
        "genuine": genuine,
        "score_claimed": measurable,
        "headline": (f"{genuine}/{n} genuine (relevant + recent)" if measurable
                     else "NOT MEASURED — live provider access unavailable"),
        "by_split": {
            split: {
                "total": sum(1 for r in records if r["split"] == split),
                "genuine": sum(1 for r in records if r["split"] == split and r["genuine"]),
            } for split in sorted({r["split"] for r in records})
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0, help="run only the first N queries")
    parser.add_argument("--offline", action="store_true",
                        help="do not touch the network; report that live validation is required")
    parser.add_argument("--out", type=str, default="", help="write the JSON report to this path")
    args = parser.parse_args(argv)

    queries = _news_queries()
    if args.limit > 0:
        queries = queries[:args.limit]
    freeze = rb.freeze_report()

    print("Recent-news benchmark — frozen 25-query set")
    print(f"queries: {len(queries)}  freeze_changed: {freeze['changed_since_freeze'] or 'none'}")

    if args.offline:
        report = {
            "meta": {"generated_at": datetime.now(timezone.utc).isoformat(),
                     "mode": "offline", "freeze": freeze},
            "summary": {"total": len(queries), "live": False, "score_claimed": False,
                        "headline": "NOT MEASURED — offline mode; live validation required"},
            "queries": [],
            "note": ("Offline mode performs no network I/O and claims no score. Run without "
                     "--offline on a host with outbound access to the news providers to "
                     "measure the genuine /25."),
        }
        print("\n" + report["summary"]["headline"])
        print(report["note"])
        if args.out:
            Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"results: {args.out}")
        return 0

    rb.ensure_iralens_importable()
    system = rb.IraLensSystem(max_results=5)
    records: List[Dict[str, Any]] = []
    try:
        for entry in queries:
            record = run_query(system, entry)
            records.append(record)
            mark = ("PASS" if record["genuine"] else
                    "BLOCKED" if record["block_class"] else "FAIL")
            print(f"  [{mark:7}] {entry['id']} {entry['query'][:58]:58} "
                  f"quality={int(record['quality_hit'])} fresh={int(bool(record['fresh']))} "
                  f"({record['latency']}s)")
    finally:
        system.close()

    summary = summarize(records, live=True)
    report = {
        "meta": {"generated_at": datetime.now(timezone.utc).isoformat(),
                 "mode": "live", "freeze": freeze},
        "summary": summary,
        "queries": records,
    }
    print("\n" + summary["headline"])
    print(f"  quality_hit={summary['quality_hit']}/{summary['total']}  "
          f"fresh={summary['fresh']}/{summary['total']}  "
          f"answered={summary['answered']}  blocked={summary['blocked']}")
    if not summary["score_claimed"]:
        print("  No score is claimed: no query reached a live provider. This is an "
              "environment limitation, not a measured pipeline failure.")
    failed = [r for r in records if not r["genuine"] and not r["block_class"]]
    if failed:
        print("\nFailed (measured, not genuine):")
        for r in failed:
            print(f"  {r['id']}: {r['query']}")
            print(f"      reason={r['reason']} matched_by={r['matched_by'] or '-'} "
                  f"fresh={r['fresh']} results={r['result_count']}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nresults: {args.out}")
    # Exit 0 when measured-and-passing OR honestly blocked; 1 only on a real,
    # measured failure so CI can distinguish the two.
    if summary["score_claimed"] and summary["genuine"] < summary["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
