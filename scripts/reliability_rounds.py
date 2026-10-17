#!/usr/bin/env python3
"""Run query rounds through IraLens and stop when reliability reaches a target.

Input: a JSONL file, one query per line:
  {"id": "q1", "query": "...", "acceptable_domains": [...], "expected_phrases": [...]}
`acceptable_domains` / `expected_phrases` are optional. A query is a success when
one of its top-N results is on an acceptable domain or contains an expected
phrase (the same rule as benchmarks/run_benchmark.py). Unlabelled queries are a
success when IraLens returns at least one result.

Every query runs with the cache bypassed. Rounds are taken in order from the
file, `--round-size` queries at a time, until a round reaches `--target` or
`--max-rounds` is hit. This script measures only; it does not change code.

    .venv/bin/python scripts/reliability_rounds.py --queries q.jsonl \
        --round-size 1000 --target 0.98 --max-rounds 5 --out results/rounds.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]


def load_queries(path: Path) -> List[Dict[str, Any]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if not item.get("query"):
            raise ValueError(f"{path}:{n}: missing 'query'")
        item.setdefault("id", f"line{n}")
        out.append(item)
    return out


def domain_ok(url: str, allowed: List[str]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


def grade(item: Dict[str, Any], response: Dict[str, Any], top_n: int) -> Dict[str, Any]:
    results = (response.get("results") or [])[:top_n]
    domains = item.get("acceptable_domains") or []
    phrases = item.get("expected_phrases") or []
    labelled = bool(domains or phrases)
    if labelled:
        hit = any(
            (domains and domain_ok(r.get("url", ""), domains))
            or any(p.casefold() in f"{r.get('title', '')} {r.get('snippet', '')}".casefold() for p in phrases)
            for r in results
        )
    else:
        hit = bool(results)
    return {"id": item["id"], "success": bool(hit), "labelled": labelled,
            "results": len(results), "reason": response.get("no_results_reason") or ""}


def make_iralens_search(max_results: int) -> Callable[[str], Dict[str, Any]]:
    sys.path.insert(0, str(ROOT))
    from iralens import IraLens  # imported lazily so --help works without deps

    lens = IraLens()

    def run(query: str) -> Dict[str, Any]:
        try:
            response = lens.search_api(query, options={
                "max_results": max_results, "reformulate": True, "cache": "bypass"})
            return response.to_dict()
        except Exception as exc:  # typed errors are recorded, never fatal
            return {"results": [], "no_results_reason": f"raised:{type(exc).__name__}"}

    return run


def run_rounds(queries: List[Dict[str, Any]], search: Callable[[str], Dict[str, Any]], *,
               round_size: int, target: float, max_rounds: int, top_n: int,
               pace: float = 0.0) -> Dict[str, Any]:
    rounds: List[Dict[str, Any]] = []
    cursor = 0
    while cursor < len(queries) and len(rounds) < max_rounds:
        batch = queries[cursor:cursor + round_size]
        cursor += len(batch)
        rows = []
        for item in batch:
            rows.append(grade(item, search(item["query"]), top_n))
            if pace:
                time.sleep(pace)
        ok = sum(1 for r in rows if r["success"])
        rate = ok / len(rows) if rows else 0.0
        rounds.append({"round": len(rounds) + 1, "queries": len(rows), "successes": ok,
                       "reliability": round(rate, 4), "meets_target": rate >= target,
                       "failures": [r for r in rows if not r["success"]]})
        if rate >= target:
            break
    reached = bool(rounds and rounds[-1]["meets_target"])
    return {"target": target, "reached": reached, "rounds": rounds}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queries", required=True, type=Path)
    ap.add_argument("--round-size", type=int, default=1000)
    ap.add_argument("--target", type=float, default=0.98)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--top", type=int, default=5, help="results graded per query")
    ap.add_argument("--pace", type=float, default=1.0, help="seconds between queries")
    ap.add_argument("--out", type=Path, default=Path("results/rounds.json"))
    args = ap.parse_args(argv)

    queries = load_queries(args.queries)
    search = make_iralens_search(max_results=args.top)
    report = run_rounds(queries, search, round_size=args.round_size, target=args.target,
                        max_rounds=args.max_rounds, top_n=args.top, pace=args.pace)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in report["rounds"]:
        print(f"round {r['round']}: {r['successes']}/{r['queries']} = {r['reliability'] * 100:.1f}%")
    print("target reached" if report["reached"] else f"target {args.target:.0%} NOT reached")
    return 0 if report["reached"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
