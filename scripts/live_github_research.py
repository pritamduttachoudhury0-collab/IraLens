#!/usr/bin/env python3
"""Live research over REAL GitHub data, through the real research planner.

This is NOT a public-web research run. Web search engines and the web reader
are not used. Instead:

  search  -> public facade op  fetch("github", "search_repos")   (live api.github.com)
  read    -> public facade op  fetch("github", "readme")         (live api.github.com)

Everything after retrieval (query expansion, evidence scoring, claim extraction,
contradiction candidates, statements, provenance, the cited report) is the real
Phase 3 research code. The report is then replayed offline from its own trace,
and the replay must reproduce the same statements and contradictions.

Needs network access to api.github.com. Unauthenticated GitHub API limits apply.

Usage:
    .venv/bin/python scripts/live_github_research.py "your question" [--out report.json]
"""
import argparse
import json
import sys

from iralens import IraLens
from iralens.research import ResearchPlanner
from iralens.research.schema import ResearchOptions
from iralens.search.schema import RankedResult, SearchResponse
from iralens.settings import Settings


def _search_fn(hil: IraLens, limit: int):
    def search(text: str) -> SearchResponse:
        hits = hil.fetch("github", "search_repos", query=text, limit=limit)
        hits = hits if isinstance(hits, list) else [hits]
        results = []
        for i, art in enumerate(hits, start=1):
            meta = art.metadata or {}
            snippet = f"{art.title} ({meta.get('language') or 'n/a'}, {meta.get('stars', 0)} stars)"
            results.append(RankedResult(
                title=art.title, url=art.url, canonical_url=art.url, snippet=snippet,
                score=round(1.0 / i, 4), score_breakdown={"position": round(1.0 / i, 4)},
                engines=["github-api"], engine_ranks={"github-api": i},
                published_at=(meta.get("updated_at") or None), date_status="known" if meta.get("updated_at") else "unknown",
                queries=[text], security_flags=[],
            ))
        return SearchResponse(
            query=text, results=results, queries=[{"text": text, "strategy": "live-github"}],
            outcomes=[], fallbacks=[], filters={}, cache={"status": "bypass"}, dedup_log=[],
            no_results_reason="none" if results else "no_results",
        )
    return search


def _reader_fn(hil: IraLens):
    def read(url: str) -> str:
        repo = url.split("github.com/", 1)[1].strip("/")
        art = hil.fetch("github", "readme", repo=repo)
        return art.content or ""
    return read


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--min-sources", type=int, default=3)
    ap.add_argument("--read-top", type=int, default=4)
    ap.add_argument("--per-query", type=int, default=6)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with IraLens() as hil:
        settings = Settings.from_config(hil.config)
        opts = ResearchOptions.build(
            {"max_rounds": args.rounds, "min_sources": args.min_sources,
             "read_top_n": args.read_top, "max_results": args.per_query},
            max_rounds=settings.research_max_rounds, max_queries=settings.research_max_queries,
            min_sources=settings.research_min_sources, max_results=settings.search_max_results,
            read_top_n=settings.research_read_top_n,
        )
        planner = ResearchPlanner(settings, _search_fn(hil, args.per_query), reader=_reader_fn(hil))
        report = planner.run(args.question, opts)
        replayed = ResearchPlanner.replay(report.to_dict(), settings)

    data = report.to_dict()
    same = (replayed.to_dict()["statements"] == data["statements"]
            and replayed.to_dict()["contradictions"] == data["contradictions"]
            and replayed.to_dict()["stop_reason"] == data["stop_reason"])
    summary = {
        "question": args.question,
        "stop_reason": data["stop_reason"],
        "rounds_run": data["rounds_run"],
        "queries": list(data["queries"]),
        "sources": [{"id": s["id"], "url": s["url"], "read_status": s["read_status"], "score": s["score"]}
                    for s in data["sources"]],
        "claims": len(data["claims"]),
        "contradictions": data["contradictions"],
        "statements": [{"status": s["status"], "confidence": s["confidence"], "text": s["text"],
                        "source_ids": s["source_ids"]} for s in data["statements"]],
        "provenance_nodes": len(data["provenance"]["nodes"]),
        "provenance_edges": len(data["provenance"]["edges"]),
        "limitations": data["limitations"],
        "replay_matches_live_run": same,
    }
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
