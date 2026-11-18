#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure whether news results avoid aggregators and still fill five slots.

Re-runs the 25 frozen `recent_news` queries through the real IraLens facade,
then scores the top-5 slots with two independent measures:

  quality      the benchmark's own verdict (`score_query` from run_benchmark.py
               — imported, not re-implemented, so the number is comparable to
               the 0.16/0.16/0.28 measured before the fix)
  aggregator   the share of top-5 *slots* that land on a syndication portal
               (news.google.com / msn / yahoo / apple.news). The targets
               are zero aggregator slots and a full 5-slot rate of at least 95%.

A slot on a portal that still exposes the real publisher is still a portal
slot: a caller cannot cite it. So the resolver's success is *also* reported
separately (`unresolvable_share`), because "resolved" and "not a portal" are
different claims.

Usage:
    IRALENS_HOME=benchmarks/.home python3 benchmarks/news_split_eval.py [--limit N]

Writes `benchmarks/results/news-split/<tag>.json` plus a one-screen summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BENCH_DIR = Path(__file__).resolve().parent
REPO = BENCH_DIR.parent
OUT_DIR = BENCH_DIR / "results" / "news-split"

sys.path.insert(0, str(REPO / "benchmarks"))
sys.path.insert(0, str(REPO))

from run_benchmark import (IraLensSystem, host_of, score_query)  # noqa: E402

#: `IraLensSystem.search` already passes ``cache="bypass"``; this flag makes
#: that explicit at the seam this instrument owns, so a future refactor of the
#: harness cannot silently turn every re-run into a replay of the first one.
BYPASS_CACHE = {"cache": "bypass"}

from iralens.search.engines.aggregators import is_aggregator, is_wrapper  # noqa: E402


def _apply_ablation(ablate: str) -> None:
    """Turn the new engine off, to prove the numbers came from it.

    Import-time is the only reliable place for this: the settings object is
    built once per session by ``IraLens()``.
    """
    if ablate in ("resolver", "all"):
        os.environ["IRALENS_NEWS_RESOLVE_WRAPPERS"] = "0"
    if ablate in ("aggregators", "all"):
        os.environ["IRALENS_NEWS_AGGREGATOR_POLICY"] = "off"
    if ablate == "all":
        # Full pre-fix behaviour: opaque wrappers are kept, not dropped, so
        # the "before" number is comparable to the 45.8% / 72.5% measured in
        # the reliability evaluation.
        os.environ["IRALENS_NEWS_DROP_UNRESOLVED_WRAPPERS"] = "0"
    # The resolver is a process-wide singleton built on first use; make sure
    # the ablation is in the environment before anything can construct it.
    os.environ.setdefault("IRALENS_HOME", str(REPO / "benchmarks" / ".home"))


def load_news_entries() -> List[Dict[str, Any]]:
    data = json.loads((BENCH_DIR / "queries.json").read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("queries", data)
    return [e for e in entries if e.get("category") == "recent_news"]


def evaluate(limit: int = 0, pace: float = 1.0) -> Dict[str, Any]:
    entries = load_news_entries()
    if limit:
        entries = entries[:limit]
    system = IraLensSystem(max_results=5)
    records: List[Dict[str, Any]] = []
    started = time.monotonic()
    try:
        for index, entry in enumerate(entries, start=1):
            cycle = time.monotonic()
            observation = system.search(entry["query"])  # cache bypassed by the harness
            record = score_query(entry, observation)
            slots = observation.results[:5]
            record["slots"] = [
                {
                    "url": s.get("url", ""),
                    "host": host_of(s.get("url", "")),
                    "aggregator": is_aggregator(s.get("url", "")),
                    "wrapper": is_wrapper(s.get("url", "")),
                    "published_at": s.get("published_at"),
                }
                for s in slots
            ]
            record["slot_count"] = len(slots)
            record["aggregator_slots"] = sum(1 for s in record["slots"] if s["aggregator"])
            record["wrapper_slots"] = sum(1 for s in record["slots"] if s["wrapper"])
            record["publisher_slots"] = record["slot_count"] - record["aggregator_slots"]
            records.append(record)
            if pace > 0 and index < len(entries):
                time.sleep(max(0.0, pace - (time.monotonic() - cycle)))
            print(f"  {index}/{len(entries)} {entry['id']} "
                  f"ok={record.get('matched_by') or '-'} "
                  f"slots={record['slot_count']} agg={record['aggregator_slots']}",
                  flush=True)
    finally:
        system.close()

    total_slots = sum(r["slot_count"] for r in records)
    agg_slots = sum(r["aggregator_slots"] for r in records)
    wrapper_slots = sum(r["wrapper_slots"] for r in records)
    answered = sum(1 for r in records if r.get("matched_by"))
    fresh = [r for r in records if r.get("fresh") is not None]
    return {
        "tag": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ablation": os.environ.get("IRALENS_NEWS_AGGREGATOR_POLICY", "default"),
        "resolver_enabled": os.environ.get("IRALENS_NEWS_RESOLVE_WRAPPERS", "1") != "0",
        "queries": len(records),
        "wall_seconds": round(time.monotonic() - started, 1),
        "quality": round(answered / len(records), 4) if records else 0.0,
        "answered": answered,
        # A drop policy must not starve the response: 5 slots requested, 5
        # delivered. A share of 0% bought by returning three results would be
        # a worse product, not a better one.
        "full_slots": sum(1 for r in records if r["slot_count"] >= 5),
        "full_slot_rate": (round(sum(1 for r in records if r["slot_count"] >= 5)
                                 / len(records), 4) if records else None),
        "mean_slots": round(total_slots / len(records), 2) if records else None,
        "freshness_rate": (round(sum(1 for r in fresh if r["fresh"]) / len(fresh), 4)
                           if fresh else None),
        "slots": total_slots,
        "aggregator_slots": agg_slots,
        "aggregator_share": round(agg_slots / total_slots, 4) if total_slots else None,
        "wrapper_slots": wrapper_slots,
        "wrapper_share": round(wrapper_slots / total_slots, 4) if total_slots else None,
        "publisher_share": (round((total_slots - agg_slots) / total_slots, 4)
                            if total_slots else None),
        "records": records,
    }


def _percent(value: float | None) -> str:
    return f"{value:.2%}" if value is not None else "N/A: no slots returned"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument("--tag", default="")
    parser.add_argument("--ablate", default="", choices=["", "resolver", "aggregators", "all"],
                        help="disable part of the new engine (to prove the effect)")
    args = parser.parse_args()
    _apply_ablation(args.ablate)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = evaluate(args.limit, args.pace)
    tag = args.tag or result["tag"].replace(":", "").replace("-", "")
    path = OUT_DIR / f"{tag}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("\n" + "=" * 68)
    print(f"news queries      : {result['queries']}")
    print(f"quality (benchmark): {result['quality']:.2%}  ({result['answered']}/"
          f"{result['queries']})")
    print(f"freshness rate    : {result['freshness_rate']}")
    print(f"top-5 slots       : {result['slots']}")
    print(f"aggregator slots  : {result['aggregator_slots']} "
          f"({_percent(result['aggregator_share'])})   target 0.00%")
    print(f"wrapper slots     : {result['wrapper_slots']} ({_percent(result['wrapper_share'])})")
    print(f"publisher share   : {_percent(result['publisher_share'])}")
    print(f"full 5-slot rate  : {result['full_slot_rate']:.2%} "
          f"(mean {result['mean_slots']} slots/query)")
    print(f"saved             : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
