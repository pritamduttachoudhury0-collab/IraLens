# -*- coding: utf-8 -*-
"""CLI exit-code contract (D-079): 0 ok, 2 caller, 3 internal, 4 engines-failed.

Deterministic: the IraLens facade is replaced with fakes, so these run the
same offline everywhere (including CI) with no network dependence.
"""

import pytest

from iralens import cli
from iralens.errors import (
    InvalidInputError,
    SearchEnginesFailedError,
)
from iralens.search.schema import SearchResponse


def _response(no_results_reason, results=()):
    return SearchResponse(
        query="q",
        results=list(results),
        queries=[],
        outcomes=[],
        fallbacks=[],
        filters={},
        cache={},
        dedup_log=[],
        no_results_reason=no_results_reason,
        summary="s",
    )


class FakeLens:
    """Duck-types the IraLens facade; behaviors configured per test."""

    search_result = []
    search_error = None
    api_response = None
    research_stop = "max_rounds"

    def __init__(self, config=None, session=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, *a, **kw):
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def search_api(self, *a, **kw):
        return self.api_response

    def research(self, *a, **kw):
        class Report:
            stop_reason = FakeLens.research_stop

            def to_dict(self):
                return {"stop_reason": self.stop_reason}

            def render_text(self):
                return f"stop: {self.stop_reason}"

        return Report()


@pytest.fixture()
def fake_lens(monkeypatch):
    monkeypatch.setattr(cli, "IraLens", FakeLens)
    FakeLens.search_result = []
    FakeLens.search_error = None
    FakeLens.api_response = _response("none")
    FakeLens.research_stop = "max_rounds"
    return FakeLens


def _run(argv):
    return cli.main(argv)


def test_success_is_zero(fake_lens):
    assert _run(["--json", "search", "q"]) == cli.EXIT_OK


def test_honest_zero_results_is_zero(fake_lens):
    """An empty but answered search is success — not an error (D-079)."""
    FakeLens.api_response = _response("no_results")
    assert _run(["--json", "search-api", "q"]) == cli.EXIT_OK


def test_search_api_engines_failed_exits_4(fake_lens):
    FakeLens.api_response = _response("engines_failed")
    assert _run(["--json", "search-api", "q"]) == cli.EXIT_ENGINES_FAILED


def test_search_api_filtered_out_is_zero(fake_lens):
    FakeLens.api_response = _response("filtered_out")
    assert _run(["--json", "search-api", "q"]) == cli.EXIT_OK


def test_simple_search_engines_failed_exits_4(fake_lens):
    FakeLens.search_error = SearchEnginesFailedError(
        "web search failed on every backend", detail="duckduckgo: captcha")
    assert _run(["search", "q"]) == cli.EXIT_ENGINES_FAILED


def test_research_search_failed_exits_4_json_and_text(fake_lens):
    FakeLens.research_stop = "search_failed"
    assert _run(["--json", "research", "q"]) == cli.EXIT_ENGINES_FAILED
    assert _run(["research", "q"]) == cli.EXIT_ENGINES_FAILED


def test_research_other_stops_exit_zero(fake_lens):
    FakeLens.research_stop = "max_rounds"
    assert _run(["--json", "research", "q"]) == cli.EXIT_OK
    FakeLens.research_stop = "min_sources"
    assert _run(["--json", "research", "q"]) == cli.EXIT_OK


def test_caller_errors_exit_2(fake_lens):
    FakeLens.search_error = InvalidInputError("too long", detail="x")
    assert _run(["search", "q"]) == cli.EXIT_CALLER
    FakeLens.search_error = ValueError("invalid filter")
    assert _run(["search", "q"]) == cli.EXIT_CALLER


def test_internal_errors_exit_3(fake_lens):
    FakeLens.search_error = RuntimeError("boom")
    assert _run(["search", "q"]) == cli.EXIT_INTERNAL


def test_keyboard_interrupt_exits_130(fake_lens):
    FakeLens.search_error = KeyboardInterrupt()
    assert _run(["search", "q"]) == cli.EXIT_INTERRUPT


def test_print_url_prints_bare_url(fake_lens, capsys):
    from iralens.engine.install import ENGINE_RELEASE_BASE, ENGINE_VERSION, _asset_name

    code = _run(["install-engine", "--print-url"])
    out = capsys.readouterr().out.strip()
    assert code == cli.EXIT_OK
    assert out == f"{ENGINE_RELEASE_BASE}/v{ENGINE_VERSION}/{_asset_name()}"
    assert " " not in out


def test_print_url_json(fake_lens, capsys):
    import json

    code = _run(["--json", "install-engine", "--print-url"])
    body = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert body["url"].startswith("https://github.com/")
    assert body["version"]
