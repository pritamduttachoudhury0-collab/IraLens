# -*- coding: utf-8 -*-
"""The audit's own failure index, and how the harness attributes it.

The 3,000-query audit's `evaluations.jsonl` records a *relevance* judgment for
every query. Only 45 of the 3,000 were judged defective; the other 2,955 are
`unresolved`, because the Arena comparison was never run for them.

The audit's own S-RL entry carries `root_status: "Unknown"`: it established
that IraLens presented unrelated filler as usable evidence (a handling defect,
ours) but could not establish why the provider returned that filler (not
ours). The harness therefore scores the two halves separately, and the test
below pins that separation so the split cannot quietly collapse back into
"everything relevant-looking is a code failure".
"""

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from benchmarks.run_audit3000 import score_record  # noqa: E402


def _record(**overrides):
    response = {
        "results": [{
            "title": "Global Learning - Boise State University",
            "url": "https://boisestate.edu/global",
            "snippet": "Global programs and courses",
            "published_at": None,
            "date_kind": "",
            "date_evidence": "",
            "engines": ["bing"],
        }],
        "outcomes": [{"engine": "bing", "status": "results", "kind": "ok"}],
        "no_results_reason": "none",
    }
    response.update(overrides.pop("response", {}))
    record = {"query_id": "QTEST", "query": "What is the capital of Assam?", "response": response}
    record.update(overrides)
    return record


def test_undisclosed_low_coverage_is_our_defect():
    """The response says "none" while the coverage verdict says "low"."""
    findings, checked = score_record(_record())
    assert any(f.family == "RL" and f.cause == "code" for f in findings), findings
    assert checked["RL"] == 1
    assert checked["RLR"] == 1


def test_disclosed_low_coverage_is_upstream_not_ours():
    """After E-22 the response discloses the gap, so it is not a code failure."""
    findings, _checked = score_record(_record(response={"no_results_reason": "low_coverage"}))
    assert not any(f.cause == "code" for f in findings), findings
    assert any(f.family == "RLR" and f.cause == "unresolved" for f in findings), findings


def test_a_well_covered_query_is_clean():
    record = _record(query="Python asyncio TaskGroup documentation")
    record["response"]["results"][0]["title"] = "asyncio TaskGroup documentation"
    record["response"]["results"][0]["snippet"] = "TaskGroup groups tasks"
    findings, _checked = score_record(record)
    assert findings == [], findings


def test_the_harness_self_test_still_passes():
    out = subprocess.run(
        [sys.executable, str(REPO / "benchmarks" / "run_audit3000.py"), "--self-test"],
        capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "self-test: ok" in out.stdout


def test_the_projection_tool_is_in_the_repository():
    """The score that is claimed must be traceable to a fix, not asserted."""
    script = REPO / "benchmarks" / "project_score.py"
    assert script.is_file()
    out = subprocess.run(
        [sys.executable, str(script), "--baseline", "/nonexistent/iralens.jsonl"],
        capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 2, out.stdout
    assert "not a measurement" in out.stdout
    assert "3,000 queries" in out.stdout or "3000 queries" in out.stdout


def test_the_projection_states_all_three_headline_numbers():
    script = REPO / "benchmarks" / "project_score.py"
    source = script.read_text(encoding="utf-8")
    for needed in ("no_code_caused_failure",
                   "no_defect_at_all_audit_manual",
                   "no_finding_at_all_strict"):
        assert needed in source, needed
    # The fix semantics must name the register entry that does the work.
    assert "E-22" in source and "E-04" in source and "E-03" in source
