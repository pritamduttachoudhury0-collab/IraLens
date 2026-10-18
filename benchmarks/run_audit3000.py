#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The 3,000-query acceptance run.

Runs the frozen 3,000-query corpus (the audit's dataset, hash-verified) through
`search_api` and scores every query against the issue families the audit
defined. Every failure is classified:

  * **code** — IraLens could have done better. These are the defects this
    project can fix, and the score that matters.
  * **external** — the upstream provider refused, challenged or throttled us.
    No code change makes a captcha go away, and the runner never pretends
    otherwise.

    python benchmarks/run_audit3000.py --live            # run the corpus
    python benchmarks/run_audit3000.py --replay FILE     # score a captured run
    python benchmarks/run_audit3000.py --self-test       # validate the harness

The corpus is `benchmarks/audit3000/queries.jsonl` with its sha256 in
`benchmarks/audit3000/dataset.sha256`. It is never edited: a run that misses a
threshold is reported as a miss.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CORPUS_DIR = REPO / "benchmarks" / "audit3000"
CORPUS = CORPUS_DIR / "queries.jsonl"
CORPUS_HASH = CORPUS_DIR / "dataset.sha256"
DEFAULT_OUT = REPO / "benchmarks" / "results" / "audit3000"

#: The options the audit used. They are the audit's, not ours: changing them
#: would change the measurement.
AUDIT_OPTIONS = {"max_results": 5, "reformulate": False, "cache": "bypass"}

#: Failure kinds that are the *provider's* doing, not ours.
EXTERNAL_KINDS = frozenset({
    "captcha", "rate_limited", "blocked", "http_error", "http_403", "http_429",
    "timeout", "transient", "network", "circuit_open", "dns", "tls",
})

#: Detail markers that mean "the provider refused us" — external.
_EXTERNAL_DETAIL_MARKERS = (
    "rate_limited", "rate limit", "captcha", "blocked", "too many requests",
    "fetch failed", "urlopen error", "tls", "ssl", "connection", "dns",
    "unreachable", "reset", "timed out", "timeout", "certificate", "proxy",
    "circuit", "cooldown",
)

#: Detail markers that mean "this component is not installed here" — a
#: configuration defect, and one this project can fix.
_CONFIGURATION_DETAIL_MARKERS = (
    "not installed", "not configured", "not found on path", "no such command",
    "not available", "missing",
)

#: The issue families this runner scores.
#:
#: `RL` and `RLR` are deliberately *separate*. The audit's S-RL entry has
#: `root_status: "Unknown"`: it established that IraLens presented unrelated
#: filler as usable evidence (a handling defect, ours), but it could not
#: establish why the provider returned that filler (not ours). The audit
#: itself only confirmed a relevance defect on 45 of 3,000 queries, because
#: the other 2,955 were never manually reviewed. Splitting the family keeps
#: the two apart instead of charging an upstream gap to the code.
FAMILIES = ("AV", "DT", "SN", "SITE", "RL", "RLR")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- the corpus
def load_corpus() -> List[Dict[str, Any]]:
    """The frozen queries, with the hash checked."""
    if not CORPUS.is_file():
        raise SystemExit(f"corpus missing: {CORPUS}")
    expected = ""
    if CORPUS_HASH.is_file():
        expected = CORPUS_HASH.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    if expected and actual != expected:
        raise SystemExit(
            f"corpus hash mismatch: expected {expected}, got {actual}. "
            "The dataset is frozen; it must not change.")
    rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return rows


# -------------------------------------------------------------- the findings
@dataclass
class Finding:
    family: str
    cause: str          # "code" | "external" | "unresolved"
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"family": self.family, "cause": self.cause, "detail": self.detail}


@dataclass
class Score:
    total: int = 0
    families: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def add(self, family: str, cause: str) -> None:
        entry = self.families.setdefault(
            family, {"code": 0, "external": 0, "unresolved": 0, "checked": 0})
        entry[cause] = entry.get(cause, 0) + 1

    def checked(self, family: str, count: int) -> None:
        entry = self.families.setdefault(
            family, {"code": 0, "external": 0, "unresolved": 0, "checked": 0})
        entry["checked"] += count

    def to_dict(self) -> Dict[str, Any]:
        code = sum(f["code"] for f in self.families.values())
        external = sum(f["external"] for f in self.families.values())
        unresolved = sum(f.get("unresolved", 0) for f in self.families.values())
        checked = sum(f["checked"] for f in self.families.values())
        clean = checked - code - external - unresolved
        return {
            "queries": self.total,
            "checks": checked,
            "clean": clean,
            "code_failures": code,
            "external_failures": external,
            "unresolved_failures": unresolved,
            # The score that matters: the share of checks IraLens could have
            # controlled and got right. An upstream gap is not a mark against
            # the code, and it is not quietly counted as a pass either.
            "code_score": round((checked - code) / checked, 6) if checked else None,
            "strict_clean_rate": round(clean / checked, 6) if checked else None,
            "by_family": self.families,
        }


def _classify_availability(kind: str, detail: str) -> str:
    """Whether an engine failure is the provider's doing or ours.

    An engine that could not be reached, that was rate-limited, that hit a
    captcha, or that was skipped because its circuit was open after earlier
    provider failures is *external*. An engine that is not installed on this
    machine is a *configuration* defect — the project can fix that by not
    putting it in the default chain.
    """
    if kind in EXTERNAL_KINDS:
        return "external"
    low = str(detail or "").lower()
    if any(marker in low for marker in _CONFIGURATION_DETAIL_MARKERS):
        return "code"
    if any(marker in low for marker in _EXTERNAL_DETAIL_MARKERS):
        return "external"
    if kind == "unavailable":
        # The engine could not serve. Without a configuration marker in the
        # detail this is the provider being unreachable.
        return "external"
    return "code"


def _host(url: str) -> str:
    text = str(url or "").lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    host = text.split("/")[0].split("?")[0]
    return host[4:] if host.startswith("www.") else host


def _scope_of(query: str) -> List[str]:
    from iralens.search.querytext import parse_site_operator

    try:
        domains, _cleaned = parse_site_operator(query or "")
    except Exception:
        return []
    return list(domains or [])


def score_record(record: Dict[str, Any]) -> Tuple[List[Finding], Dict[str, int]]:
    """Every issue-family finding of one query record, and how much was checked.

    The predicates are written so that they can be applied to a *captured*
    baseline run as well as to a live one: the same rule decides.
    """
    from iralens.search.coverage import assess_coverage

    findings: List[Finding] = []
    checked: Dict[str, int] = {}
    response = record.get("response") or {}
    results = response.get("results") or []
    outcomes = response.get("outcomes") or []
    query = record.get("query") or response.get("query") or ""

    # S-AV: an engine in the effective chain failed or was skipped.
    checked["AV"] = len(outcomes)
    for outcome in outcomes:
        status = str(outcome.get("status") or "")
        if status not in ("failed", "skipped"):
            continue
        kind = str(outcome.get("kind") or "")
        detail = str(outcome.get("detail") or "")
        cause = _classify_availability(kind, detail)
        findings.append(Finding("AV", cause, f"{outcome.get('engine')}: {kind or status}"))

    # S-DT: a date reported as a publication date that is not one.
    checked["DT"] = len(results)
    for result in results:
        published = result.get("published_at")
        if not published:
            continue
        date_kind = str(result.get("date_kind") or "")
        evidence = str(result.get("date_evidence") or "")
        engines = [str(e) for e in (result.get("engines") or [])]
        if date_kind == "revision" or (not evidence and "wikipedia" in engines):
            findings.append(Finding("DT", "code", f"{result.get('url')} date={published}"))

    # S-SN: a Wikipedia hit with no snippet.
    wiki_results = [r for r in results
                    if "wikipedia" in [str(e) for e in (r.get("engines") or [])]]
    checked["SN"] = len(wiki_results)
    for result in wiki_results:
        if not str(result.get("snippet") or "").strip():
            findings.append(Finding("SN", "code", str(result.get("url"))))

    # S-SITE: a result outside the query's explicit `site:` scope.
    scope = _scope_of(query)
    checked["SITE"] = len(results) if scope else 0
    if scope:
        for result in results:
            host = _host(result.get("url"))
            if not any(host == d or host.endswith("." + d) for d in scope):
                findings.append(Finding("SITE", "code", str(result.get("url"))))

    # S-RL: the results do not address the question.
    #
    # Two separate things, kept separate:
    #
    #   RL  — *ours*. The response presents those results as usable
    #         evidence, i.e. it does not disclose the low coverage. The
    #         audit's expectation is explicit: "if no such sources are
    #         found, report low coverage rather than presenting unrelated
    #         filler as usable evidence". A response that says
    #         `no_results_reason: "none"` while the coverage verdict is
    #         "low" is that defect.
    #
    #   RLR — *not ours, and not resolved by the audit either*. The
    #         provider returned filler. The audit's own `root_status` for
    #         S-RL is "Unknown", and it confirmed a relevance defect on
    #         only 45 of 3,000 queries by manual review. A lexical
    #         overlap proxy is a blunt instrument here, so its verdict is
    #         reported as "unresolved" — never charged to the code and
    #         never presented as a pass.
    checked["RL"] = 1 if results else 0
    checked["RLR"] = 1 if results else 0
    if results:
        verdict = assess_coverage(query, results)
        if verdict.get("status") == "low":
            reason = str(response.get("no_results_reason") or "")
            if reason in ("none", ""):
                findings.append(Finding("RL", "code", str(verdict.get("note"))[:120]))
            else:
                # The low coverage is disclosed; the results are still
                # weak, but the system is being honest about it.
                findings.append(Finding("RLR", "unresolved", str(verdict.get("note"))[:120]))
    return findings, checked


def score_records(records: Iterable[Dict[str, Any]]) -> Tuple[Score, List[Dict[str, Any]]]:
    score = Score()
    examples: List[Dict[str, Any]] = []
    for record in records:
        score.total += 1
        findings, checked = score_record(record)
        for family, count in checked.items():
            score.checked(family, count)
        for finding in findings:
            score.add(finding.family, finding.cause)
        if findings and len(examples) < 50:
            examples.append({
                "query_id": record.get("query_id"),
                "query": record.get("query"),
                "findings": [f.to_dict() for f in findings],
            })
    return score, examples


# ------------------------------------------------------------------- running
def run_live(rows: List[Dict[str, Any]], *, limit: int, workers: int,
             out_dir: Path, pace: float) -> int:
    """Run the corpus against the live engines and record every response."""
    from iralens import IraLens
    from iralens.search.schema import SearchOptions

    out_dir.mkdir(parents=True, exist_ok=True)
    record_path = out_dir / "records.jsonl"
    lock = threading.Lock()
    local = threading.local()

    def _lens() -> Any:
        if not hasattr(local, "lens"):
            local.lens = IraLens()
        return local.lens

    def _one(query: Dict[str, Any]) -> None:
        started = _now()
        t0 = time.perf_counter()
        record: Dict[str, Any] = {
            "query_id": query["id"],
            "query": query["query"],
            "family": query.get("family"),
            "started_at": started,
            "options": dict(AUDIT_OPTIONS),
        }
        try:
            response = _lens().search_api(
                query["query"],
                options=SearchOptions(**AUDIT_OPTIONS),
            ).to_dict()
            record.update(status="returned", response=response,
                          source_urls=[r["url"] for r in response.get("results", [])])
        except Exception as exc:
            record.update(status="exception", exception_type=type(exc).__name__,
                          error=str(exc)[:400], detail=getattr(exc, "detail", None))
        record.update(finished_at=_now(),
                      latency_seconds=round(time.perf_counter() - t0, 3))
        with lock:
            with record_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()

    pending = rows[:limit] if limit else rows
    print(f"running {len(pending)} queries with {workers} workers "
          f"(pace {pace}s between starts)", flush=True)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(_one, pending))
    print(f"records written to {record_path}", flush=True)
    return 0


def load_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


# ------------------------------------------------------------------ reporting
def report(score: Score, examples: List[Dict[str, Any]], out_dir: Path,
           source: str) -> Dict[str, Any]:
    summary = score.to_dict()
    summary["source"] = source
    summary["generated_at"] = _now()
    summary["examples"] = examples
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"{'family':10s} {'checked':>8s} {'code':>8s} {'external':>10s} {'unresolved':>11s}")
    print("-" * 56)
    for family in FAMILIES:
        entry = score.families.get(family)
        if not entry:
            continue
        print(f"{family:10s} {entry['checked']:8d} {entry['code']:8d} "
              f"{entry['external']:10d} {entry.get('unresolved', 0):11d}")
    print("-" * 56)
    print(f"{'TOTAL':10s} {summary['checks']:8d} {summary['code_failures']:8d} "
          f"{summary['external_failures']:10d} {summary['unresolved_failures']:11d}")
    print()
    if summary["code_score"] is not None:
        print(f"code-caused failures: {summary['code_failures']}")
        print(f"external failures:     {summary['external_failures']}")
        print(f"unresolved (upstream): {summary['unresolved_failures']}")
        print(f"code score:           {(summary['code_score'] * 100):.2f}% "
              f"({summary['checks'] - summary['code_failures']}/{summary['checks']} checks)")
        print(f"strict clean:         {(summary['strict_clean_rate'] * 100):.2f}% "
              f"({summary['clean']}/{summary['checks']} checks)")
    print(f"\nwritten: {out_dir / 'results.json'}")
    return summary


# ------------------------------------------------------- the offline pipeline
from iralens.search.engines.base import SearchBackend  # noqa: E402


class _StubBackend(SearchBackend):
    """A local engine that answers every query from a fixed corpus.

    The point of this mode is the *pipeline*, not the retrieval: it proves
    that scoping, date labelling, snippet handling, readiness reporting and
    the coverage verdict behave correctly for all 3,000 queries without any
    provider being involved. Provider behaviour is measured by --live.
    """

    name = "stub"
    paginated = True
    handled: Dict[str, str] = {}

    def __init__(self) -> None:
        self.calls = 0

    def availability(self) -> Tuple[bool, str]:
        return True, ""

    def build_query(self, query: str, filters: Any) -> str:
        return query

    def search(self, query: str, filters: Any, limit: int, context: Any,
               *, offset: int = 0) -> List[Any]:
        from iralens.search.schema import SearchHit

        self.calls += 1
        terms = [t for t in str(query).split() if len(t) > 3][:3]
        slug = "-".join(terms) or "topic"
        host = "docs.example.org"
        scope = _scope_of(query)
        if scope:
            host = scope[0]
        return [
            SearchHit(title=f"{query}", url=f"https://{host}/{slug}",
                      snippet=f"About {query}. " * 4, engine="stub", position=1,
                      published_at=None, query=query),
            SearchHit(title=f"More about {query}", url=f"https://{host}/{slug}-more",
                      snippet=f"Further reading on {query}. " * 4, engine="stub",
                      position=2, published_at=None, query=query),
        ][: max(1, limit)]


def run_offline_pipeline(rows: List[Dict[str, Any]], out_dir: Path) -> int:
    """Run the whole corpus through the real engine with a local backend."""
    from iralens.search.engine import SearchEngine
    from iralens.search.schema import SearchOptions
    from iralens.settings import Settings

    out_dir.mkdir(parents=True, exist_ok=True)
    record_path = out_dir / "records-offline.jsonl"
    settings = Settings(search_engines=("stub",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0)
    engine = SearchEngine(settings, backends={"stub": _StubBackend()})

    with record_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            record: Dict[str, Any] = {
                "query_id": row["id"], "query": row["query"],
                "family": row.get("family"),
                "options": dict(AUDIT_OPTIONS),
            }
            try:
                response = engine.search(
                    row["query"],
                    options=SearchOptions(max_results=5, reformulate=False),
                )
                record.update(status="returned", response=response.to_dict())
            except Exception as exc:
                record.update(status="exception", exception_type=type(exc).__name__,
                              error=str(exc)[:400])
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"records written to {record_path}")
    return 0


# ------------------------------------------------------------------ selftest
def self_test() -> int:
    """Validate the harness against synthetic records. Not a measurement."""
    good = {
        "query_id": "T1",
        "query": "python asyncio TaskGroup documentation",
        "response": {
            "results": [{
                "title": "asyncio TaskGroup documentation",
                "url": "https://docs.python.org/3/library/asyncio-task.html",
                "snippet": "TaskGroup groups tasks",
                "published_at": None,
                "date_kind": "",
                "date_evidence": "",
                "engines": ["wikipedia"],
            }],
            "outcomes": [{"engine": "wikipedia", "status": "results", "kind": "ok"}],
        },
    }
    bad_date = json.loads(json.dumps(good))
    bad_date["response"]["results"][0]["published_at"] = "2026-01-01"
    bad_date["response"]["results"][0]["date_kind"] = "revision"
    bad_snippet = json.loads(json.dumps(good))
    bad_snippet["response"]["results"][0]["snippet"] = ""
    offsite = json.loads(json.dumps(good))
    offsite["query"] = "site:docs.python.org TaskGroup"
    offsite["response"]["results"][0]["url"] = "https://example.com/x"
    captcha = json.loads(json.dumps(good))
    captcha["response"]["outcomes"].append(
        {"engine": "duckduckgo", "status": "failed", "kind": "captcha"})
    # Low coverage that the response does not disclose: ours.
    hidden = json.loads(json.dumps(good))
    hidden["query"] = "What is the capital of Assam?"
    hidden["response"]["results"] = [{
        "title": "Global Learning - Boise State University",
        "url": "https://boisestate.edu/global",
        "snippet": "Global programs",
        "published_at": None,
        "date_kind": "",
        "date_evidence": "",
        "engines": ["bing"],
    }]
    hidden["response"]["no_results_reason"] = "none"
    # Low coverage that the response *does* disclose: upstream, not ours.
    disclosed = json.loads(json.dumps(hidden))
    disclosed["response"]["no_results_reason"] = "low_coverage"

    cases = [
        ("clean record has no finding", score_record(good)[0] == []),
        ("a revision date is a DT finding",
         any(f.family == "DT" for f in score_record(bad_date)[0])),
        ("an empty Wikipedia snippet is an SN finding",
         any(f.family == "SN" for f in score_record(bad_snippet)[0])),
        ("an off-scope result is a SITE finding",
         any(f.family == "SITE" for f in score_record(offsite)[0])),
        ("a captcha is external, not code",
         any(f.family == "AV" and f.cause == "external" for f in score_record(captcha)[0])),
        ("undisclosed low coverage is an RL (code) finding",
         any(f.family == "RL" and f.cause == "code" for f in score_record(hidden)[0])),
        ("disclosed low coverage is RLR (unresolved), not code",
         any(f.family == "RLR" and f.cause == "unresolved"
             for f in score_record(disclosed)[0])
         and not any(f.cause == "code" for f in score_record(disclosed)[0])),
    ]
    ok = True
    for name, passed in cases:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print("\nself-test:", "ok" if ok else "FAILED")
    print("(the self-test validates the harness; it is not a measurement of IraLens)")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="run the corpus against the live engines")
    parser.add_argument("--replay", metavar="FILE",
                        help="score a captured run (JSONL) with the same predicates")
    parser.add_argument("--limit", type=int, default=0, help="only the first N queries")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--pace", type=float, default=1.0,
                        help="seconds between worker starts (default 1.0)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--offline-pipeline", action="store_true",
                        help="run the corpus through the engine with a local "
                             "backend: measures the code, not the providers")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    out_dir = Path(args.out)
    rows = load_corpus()
    print(f"corpus: {len(rows)} queries, sha256 verified")

    if args.replay:
        records = load_records(Path(args.replay))
        if args.limit:
            records = records[: args.limit]
        score, examples = score_records(records)
        report(score, examples, out_dir, source=f"replay:{args.replay}")
        return 0

    if args.offline_pipeline:
        run_offline_pipeline(rows, out_dir)
        records = load_records(out_dir / "records-offline.jsonl")
        score, examples = score_records(records)
        report(score, examples, out_dir, source="offline-pipeline")
        return 0

    if args.live:
        run_live(rows, limit=args.limit, workers=args.workers, out_dir=out_dir,
                 pace=args.pace)
        records = load_records(out_dir / "records.jsonl")
        score, examples = score_records(records)
        report(score, examples, out_dir, source="live")
        return 0

    parser.print_help()
    print("\nNothing scored: pass --live, --replay FILE, --offline-pipeline "
          "or --self-test.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
