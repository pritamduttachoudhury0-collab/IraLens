#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Project the 3,000-query score: what should it be, and why.

This is a **projection, not a measurement**. It replays the audit's captured
baseline, applies the documented fix semantics of each register entry, and
reports the per-query outcome under two provider conditions. A live run
(`run_audit3000.py --live`) is the only way to *measure* the score; this tool
exists so the number that is being claimed can be traced back to a specific
fix rather than asserted.

    python benchmarks/project_score.py
    python benchmarks/project_score.py --baseline /path/to/iralens.jsonl

The baseline it reads is the audit's own captured 3,000-query run. It lives
outside this repository (it is the audit's evidence bundle); pass `--baseline`
to point at it. Without it the tool still reports the corpus facts and the
fix mapping.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.run_audit3000 import (  # noqa: E402
    load_corpus,
    score_record,
)

DEFAULT_BASELINE = Path("/tmp/audit-bundle/iralens-audit/records/iralens.jsonl")
DEFAULT_FAILURES = Path("/tmp/audit-bundle/iralens-audit/records/failures.jsonl")
DEFAULT_EVALUATIONS = Path("/tmp/audit-bundle/iralens-audit/records/evaluations.jsonl")
OUT_DIR = REPO / "benchmarks" / "results" / "project_score"

#: How each fix removes each family, and what remains afterwards.
#:
#: `gone`      — the defect cannot recur; the fix removes it at the source.
#: `external`  — what remains is the provider's doing.
#: `unresolved`— what remains is an upstream gap the audit itself could not
#:               attribute, and which the fixed system now *discloses*.
FIX_SEMANTICS: Dict[str, Dict[str, Any]] = {
    "AV": {
        "entries": "E-23, E-21",
        "code": "gone",
        "code_note": "the unconfigured `semantic-search` engine leaves the default "
                     "chain, so 'not installed' is no longer reported as a failure",
        "external": "external",
        "external_note": "a captcha or a provider 429 is the provider's doing; "
                         "E-21 only stops us causing one by bursting",
    },
    "DT": {
        "entries": "E-04",
        "code": "gone",
        "code_note": "a Wikipedia revision timestamp is reported as `modified_at` "
                     "with `date_kind=\"revision\"`; `published_at` stays empty, so "
                     "a revision can never masquerade as a publication date",
        "external": "gone",
        "external_note": "",
    },
    "SN": {
        "entries": "E-03",
        "code": "gone",
        "code_note": "the API is asked for `srprop=snippet|timestamp`; the audit's "
                     "own independent probe confirmed the same query returns a snippet",
        "external": "gone",
        "external_note": "",
    },
    "SITE": {
        "entries": "E-14, E-15",
        "code": "gone",
        "code_note": "the `site:` scope survives tokenizing and reformulation and is "
                     "enforced on the results, so an out-of-scope hit cannot be returned",
        "external": "gone",
        "external_note": "",
    },
    "RL": {
        "entries": "E-22",
        "code": "gone",
        "code_note": "when the coverage verdict is `low` the response now says "
                     "`no_results_reason: \"low_coverage\"` and the summary warns "
                     "that the results are generic filler; research coverage can no "
                     "longer reach success on hits that do not address the question",
        "external": "unresolved",
        "external_note": "the *results* are still weak, and the audit could not "
                         "attribute that upstream (its own S-RL `root_status` is "
                         "\"Unknown\"). The fixed system reports it instead of "
                         "presenting filler as evidence",
    },
}


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help="the audit's captured 3,000-query run (JSONL)")
    parser.add_argument("--failures", type=Path, default=DEFAULT_FAILURES,
                        help="the audit's failure index (JSONL)")
    parser.add_argument("--evaluations", type=Path, default=DEFAULT_EVALUATIONS,
                        help="the audit's per-query evaluations (JSONL)")
    args = parser.parse_args()

    corpus = load_corpus()
    n = len(corpus)
    print("=" * 78)
    print("PROJECTION — not a measurement. A live run is the only measurement.")
    print("=" * 78)
    print(f"corpus: {n} queries (sha256 verified)")

    records = _load_jsonl(args.baseline)
    failures = _load_jsonl(args.failures)
    evaluations = _load_jsonl(args.evaluations)
    if not records:
        print(f"\nbaseline not found at {args.baseline}")
        print("The audit's captured run is not part of this repository.")
        print("Pass --baseline /path/to/iralens.jsonl to project the score.")
        return 2

    # ---- 1. the audit's own failure index -------------------------------
    index: Dict[str, Set[str]] = defaultdict(set)
    local_fixtures: List[Dict[str, Any]] = []
    for f in failures:
        qid = f.get("query_id")
        if qid is None:
            # A few of the audit's failure records are controlled offline
            # fixtures (a classifier probe, a two-writer session probe). They
            # are not attached to a query, so they are reported separately
            # rather than counted as a 3,001st query.
            local_fixtures.append(f)
            continue
        index[str(qid)].add(str(f.get("issue_id")))
    confirmed_defective: Set[str] = set()
    unresolved_relevance: Set[str] = set()
    for e in evaluations:
        judgment = (e.get("dimensions") or {}).get("relevance", {}).get("judgment")
        if judgment == "defective_unrelated_or_generic_filler":
            confirmed_defective.add(str(e.get("query_id")))
        elif judgment == "unresolved":
            unresolved_relevance.add(str(e.get("query_id")))

    print("\n1. THE AUDIT'S OWN FAILURE INDEX (as captured)")
    print("-" * 78)
    if index:
        fam_queries: Counter = Counter()
        for _qid, fams in index.items():
            for fam in fams:
                fam_queries[fam] += 1
        for fam, count in fam_queries.most_common():
            print(f"   {fam:<10} {count:>5} queries")
        with_fail = sum(1 for fams in index.values() if fams)
        print(f"   {'TOTAL':<10} {with_fail:>5} of {n} queries carry at least one failure")
        if local_fixtures:
            print(f"   plus {len(local_fixtures)} controlled offline fixtures "
                  f"(not query-scoped): "
                  f"{', '.join(sorted(str(f.get('failure_id')) for f in local_fixtures))}")
    else:
        print("   (failure index not available)")

    # ---- 2. score the baseline with the current predicates --------------
    from iralens.search.coverage import assess_coverage

    per_query: List[Dict[str, Any]] = []
    for rec in records:
        findings, checked = score_record(rec)
        code = Counter(f.family for f in findings if f.cause == "code")
        external = Counter(f.family for f in findings if f.cause == "external")
        unresolved = Counter(f.family for f in findings if f.cause == "unresolved")
        response = rec.get("response") or {}
        results = response.get("results") or []
        query = rec.get("query") or response.get("query") or ""
        low = bool(results) and assess_coverage(query, results).get("status") == "low"
        per_query.append({
            "query_id": rec.get("query_id"),
            "code": code, "external": external, "unresolved": unresolved,
            "checked": checked,
            "results": len(results),
            "low_coverage": low,
        })

    print("\n2. THE SAME BASELINE, SCORED WITH TODAY'S PREDICATES")
    print("-" * 78)
    tot_code = sum(sum(r["code"].values()) for r in per_query)
    tot_ext = sum(sum(r["external"].values()) for r in per_query)
    tot_unr = sum(sum(r["unresolved"].values()) for r in per_query)
    tot_checks = sum(sum(r["checked"].values()) for r in per_query)
    print(f"   checks            {tot_checks}")
    print(f"   code-caused       {tot_code}")
    print(f"   external          {tot_ext}")
    print(f"   unresolved        {tot_unr}")
    print(f"   queries with >=1 code-caused failure: "
          f"{sum(1 for r in per_query if r['code'])}")

    # ---- 3. apply the fix semantics -------------------------------------
    print("\n3. FIX SEMANTICS — what each register entry removes")
    print("-" * 78)
    for fam, spec in FIX_SEMANTICS.items():
        print(f"   {fam:<6} {spec['entries']:<12} code -> {spec['code']:<10} "
              f"external -> {spec['external']}")

    def project(providers_healthy: bool) -> Dict[str, int]:
        """Per-query outcome after the fixes.

        For each family the fix semantics above decide what survives:
        DT, SN, SITE and the code half of AV are removed at the source; the
        code half of RL is removed because the response now discloses low
        coverage; the provider half of AV survives only if the provider is
        degraded.
        """
        out = Counter()
        for r in per_query:
            code = set(r["code"])
            external = set(r["external"])
            unresolved = set(r["unresolved"])

            code -= {"DT", "SN", "SITE", "AV", "RL"}
            external -= {"DT", "SN", "SITE", "RL"}
            if providers_healthy:
                external -= {"AV"}
            if r["low_coverage"]:
                # After E-22 the response discloses the gap, so this stops
                # being "we hid it" and becomes "the results are weak" — an
                # upstream gap the audit could not attribute.
                unresolved.add("RLR")

            out["queries"] += 1
            if code:
                out["code_fail"] += 1
            elif external:
                out["external_fail"] += 1
            elif unresolved:
                out["unresolved_only"] += 1
            else:
                out["clean"] += 1
        return dict(out)

    healthy = project(True)
    degraded = project(False)

    print("\n4. PROJECTED PER-QUERY OUTCOME")
    print("-" * 78)
    for label, proj in (("providers healthy", healthy), ("providers degraded", degraded)):
        print(f"   {label:<20} clean {proj.get('clean', 0):>5}/{n}   "
              f"code-fail {proj.get('code_fail', 0):>5}   "
              f"external-fail {proj.get('external_fail', 0):>5}   "
              f"unresolved-only {proj.get('unresolved_only', 0):>5}")

    # ---- 5. the three headline numbers ----------------------------------
    print("\n5. THE THREE HEADLINE NUMBERS (out of 3,000)")
    print("-" * 78)
    a = healthy.get("code_fail", 0)
    b = confirmed_defective
    print(f"   (a) no CODE-caused failure   {n - a:>5}/{n}  = {(n - a) / n * 100:5.1f}%")
    print("       what the project controls; the number that should be 100%")
    print(f"   (b) no defect at all         {n - len(b):>5}/{n}  = "
          f"{(n - len(b)) / n * 100:5.1f}%")
    print(f"       using the audit's own MANUALLY CONFIRMED relevance defects "
          f"({len(b)} of {n})")
    print(f"   (c) no finding at all        {healthy.get('clean', 0):>5}/{n}  = "
          f"{healthy.get('clean', 0) / n * 100:5.1f}%")
    print(f"       strictest reading: the lexical coverage proxy still objects to "
          f"{healthy.get('unresolved_only', 0)} queries")

    # ---- 6. proxy calibration -------------------------------------------
    if confirmed_defective:
        flagged = {r["query_id"] for r in per_query if r["low_coverage"]}
        tp = len(flagged & confirmed_defective)
        fp = len(flagged - confirmed_defective)
        fn = len(confirmed_defective - flagged)
        print("\n6. CALIBRATION — the lexical proxy vs the audit's manual review")
        print("-" * 78)
        print(f"   proxy flags {len(flagged):>5} queries; the audit confirmed "
              f"{len(confirmed_defective):>4} by hand")
        print(f"   precision {tp / max(tp + fp, 1):.3f}   recall {tp / max(tp + fn, 1):.3f}")
        print(f"   {len(unresolved_relevance)} of the {n} queries were never "
              f"manually reviewed at all")
        print("   => the proxy is a blunt instrument. It is reported as "
              "'unresolved', never as a pass and never as a code failure.")

    # ---- 7. write the machine-readable result ---------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "PROJECTION, not a measurement. Only a live run measures the score.",
        "queries": n,
        "audit_failure_index": {
            "queries_with_any_failure": sum(1 for f in index.values() if f),
            "by_family": dict(Counter(
                fam for fams in index.values() for fam in fams)),
        },
        "baseline_scored": {
            "checks": tot_checks, "code": tot_code, "external": tot_ext,
            "unresolved": tot_unr,
        },
        "projected": {
            "providers_healthy": healthy,
            "providers_degraded": degraded,
        },
        "headline": {
            "no_code_caused_failure": f"{n - a}/{n}",
            "no_defect_at_all_audit_manual": f"{n - len(b)}/{n}",
            "no_finding_at_all_strict": f"{healthy.get('clean', 0)}/{n}",
        },
        "fix_semantics": FIX_SEMANTICS,
    }
    out = OUT_DIR / "projection.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
