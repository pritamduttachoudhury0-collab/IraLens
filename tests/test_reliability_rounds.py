import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "reliability_rounds", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "reliability_rounds.py")
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def _q(i, **kw):
    return {"id": f"q{i}", "query": f"query {i}", **kw}


def test_grade_labelled_domain_and_phrase():
    item = _q(1, acceptable_domains=["example.org"], expected_phrases=["Canberra"])
    resp = {"results": [{"url": "https://a.com/x", "title": "t", "snippet": "none"},
                        {"url": "https://www.example.org/y", "title": "t", "snippet": ""}]}
    assert rr.grade(item, resp, 5)["success"] is True
    assert rr.grade(item, {"results": [{"url": "https://a.com", "title": "x", "snippet": "y"}]}, 5)["success"] is False


def test_grade_unlabelled_counts_any_result():
    assert rr.grade(_q(2), {"results": [{"url": "https://a.com"}]}, 5)["success"] is True
    assert rr.grade(_q(2), {"results": [], "no_results_reason": "engines_failed"}, 5)["success"] is False


def test_rounds_stop_at_target_and_report_failures():
    queries = [_q(i) for i in range(6)]
    answers = {f"query {i}": ({"results": [{"url": "https://a.com"}]} if i != 0 else {"results": []})
               for i in range(6)}
    rep = rr.run_rounds(queries, lambda q: answers[q], round_size=3, target=0.6, max_rounds=5, top_n=5)
    assert rep["reached"] is True
    assert len(rep["rounds"]) == 1 and rep["rounds"][0]["successes"] == 2
    assert rep["rounds"][0]["failures"][0]["id"] == "q0"


def test_rounds_do_not_reach_target_within_max_rounds():
    queries = [_q(i) for i in range(4)]
    rep = rr.run_rounds(queries, lambda q: {"results": []}, round_size=2, target=0.98, max_rounds=2, top_n=5)
    assert rep["reached"] is False and len(rep["rounds"]) == 2
