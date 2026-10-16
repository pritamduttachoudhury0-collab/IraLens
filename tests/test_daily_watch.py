"""Watch health must not count cached data or dump page content into artifacts."""

from types import SimpleNamespace

from scripts.daily_watch import CHECKS, run_watch


class FakeLens:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []

    def read(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("GitHub unavailable")
        return SimpleNamespace(content="IraLens private canary marker", retrieval_method="direct")


def test_watch_checks_both_urls_sequentially_without_dumping_content():
    lens = FakeLens()
    sleeps = []
    out = run_watch(lens, sleep=sleeps.append, clock=lambda: 1.25)
    assert out["overall"] == "pass"
    assert len(out["checks"]) == len(CHECKS) == 2
    assert len(sleeps) == 1 and sleeps[0] >= 1
    assert all(kw["mode"] == "static" for _, kw in lens.calls)
    assert "private canary marker" not in str(out)
    assert out["checks"][0]["content_chars"] > 0


def test_watch_records_failure_but_still_reads_next_site():
    lens = FakeLens(fail_at=1)
    out = run_watch(lens, sleep=lambda s: None)
    assert out["overall"] == "fail"
    assert out["checks"][0]["error_type"] == "RuntimeError"
    assert out["checks"][1]["pass"] is True
    assert len(lens.calls) == 2
