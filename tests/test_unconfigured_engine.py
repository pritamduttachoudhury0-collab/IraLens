# -*- coding: utf-8 -*-
"""An engine whose prerequisite is missing must not trip the breaker or be called."""

from iralens.reliability import CircuitBreaker, RetryPolicy
from iralens.search.engines.base import SearchBackend
from iralens.search.fallback import KIND_NOT_CONFIGURED, run_chain
from iralens.search.schema import SearchFilters, SearchHit


class _Missing(SearchBackend):
    name = "semantic-search"
    calls = 0

    def available(self):
        return False

    def search(self, query, filters, limit, context):
        _Missing.calls += 1
        raise AssertionError("unavailable backend must not be called")


class _Ok(SearchBackend):
    name = "wikipedia"

    def search(self, query, filters, limit, context):
        return [SearchHit(title="t", url="https://en.wikipedia.org/wiki/X", snippet="s",
                          engine=self.name, position=1, query=query)]


def test_unavailable_backend_is_skipped_without_call_or_breaker_failure():
    breaker = CircuitBreaker(threshold=1, cooldown=300)
    backends = {"semantic-search": _Missing(), "wikipedia": _Ok()}
    for _ in range(5):
        res = run_chain("q", SearchFilters(), 5, ["semantic-search", "wikipedia"], backends, None,
                        min_engines=1, policy=RetryPolicy(1, 0, 0), breaker=breaker)
    assert _Missing.calls == 0
    assert breaker.state("semantic-search") == "closed"
    skipped = [o for o in res.outcomes if o.engine == "semantic-search"]
    assert skipped and skipped[0].kind == KIND_NOT_CONFIGURED and skipped[0].status == "skipped"
    assert res.succeeded == ["wikipedia"]


def test_exa_backend_reports_unavailable_without_mcporter(monkeypatch):
    from iralens.search.engines import exa
    monkeypatch.setattr(exa.shutil, "which", lambda name: None)
    assert exa.ExaBackend().available() is False
    monkeypatch.setattr(exa.shutil, "which", lambda name: "/usr/bin/mcporter")
    assert exa.ExaBackend().available() is True
