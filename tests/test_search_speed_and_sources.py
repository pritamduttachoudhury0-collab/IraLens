"""Unit tests for Search concurrency expansion, polite per-site limits,
short-term answer cache, and engine independence. Offline."""

import threading
import time
from typing import Any, List

import pytest

from iralens.cache import ResponseCache
from iralens.reliability import PerSiteConcurrencyGate, OperationTimeoutError
from iralens.search.engine import SearchEngine
from iralens.search.engines.base import SearchBackend
from iralens.search.schema import SearchFilters, SearchHit, SearchOptions
from iralens.settings import Settings


class MockSearchBackend(SearchBackend):
    def __init__(self, name: str, delay: float = 0.05, hits: int = 3) -> None:
        self.name = name
        self.delay = delay
        self.hits_count = hits
        self.calls = 0
        self._lock = threading.Lock()

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any) -> List[SearchHit]:
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return [
            SearchHit(
                title=f"{self.name} result {i} for {query}",
                url=f"https://{self.name}.example.org/item/{i}",
                snippet=f"Snippet {i} from {self.name}",
                engine=self.name,
                position=i,
                query=query,
            )
            for i in range(1, self.hits_count + 1)
        ]


def test_per_site_concurrency_gate():
    gate = PerSiteConcurrencyGate(limit=2, acquire_timeout=0.1)
    # Acquire 2 slots for siteA
    with gate.guard("siteA"):
        with gate.guard("siteA"):
            # Third acquire on siteA should raise OperationTimeoutError
            with pytest.raises(OperationTimeoutError):
                with gate.guard("siteA", timeout=0.02):
                    pass
            # But siteB is independent and should succeed
            with gate.guard("siteB"):
                pass


def test_twenty_concurrent_searches_with_cache(tmp_path):
    settings = Settings(
        search_max_concurrent=20,
        search_per_site_concurrent=5,
        cache_search_ttl_seconds=300,
        search_engines=("mock1", "mock2"),
        search_min_engines=1,
    )
    b1 = MockSearchBackend("mock1", delay=0.02)
    b2 = MockSearchBackend("mock2", delay=0.02)
    backends = {"mock1": b1, "mock2": b2}
    cache = ResponseCache(tmp_path / "cache")
    engine = SearchEngine(settings, backends=backends, cache=cache)

    results = []
    latencies = []
    errors = []

    def run_one(q: str):
        t0 = time.monotonic()
        try:
            resp = engine.search(q, options=SearchOptions(engines=("mock1", "mock2"), cache="use"))
            latencies.append(time.monotonic() - t0)
            results.append(resp)
        except Exception as exc:
            errors.append(exc)

    # 20 searches at once (10 distinct queries, 10 repeats)
    queries = [f"query_{i % 10}" for i in range(20)]
    threads = [threading.Thread(target=run_one, args=(q,)) for q in queries]
    t_start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_total = time.monotonic() - t_start

    assert not errors
    assert len(results) == 20
    # Total wall-clock time for all 20 should be well under 6 seconds (typically <1s)
    assert t_total < 6.0
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    p50 = latencies[int(len(latencies) * 0.50)]
    assert p95 < 6.0
    assert p50 < 3.0


def test_search_works_when_duckduckgo_switched_off(tmp_path):
    # Only bing and wikipedia configured
    settings = Settings(
        search_engines=("bing", "wikipedia"),
        search_min_engines=1,
    )
    b_bing = MockSearchBackend("bing", delay=0.01)
    b_wiki = MockSearchBackend("wikipedia", delay=0.01)
    backends = {"bing": b_bing, "wikipedia": b_wiki}
    engine = SearchEngine(settings, backends=backends, cache=ResponseCache(tmp_path / "c"))

    resp = engine.search("python programming", options=SearchOptions(engines=("bing", "wikipedia")))
    assert resp.results
    engines_used = {r.engines[0] for r in resp.results}
    assert "duckduckgo" not in engines_used
    assert "bing" in engines_used or "wikipedia" in engines_used


def test_search_works_when_bing_switched_off(tmp_path):
    # Only duckduckgo and wikipedia configured
    settings = Settings(
        search_engines=("duckduckgo", "wikipedia"),
        search_min_engines=1,
    )
    b_ddg = MockSearchBackend("duckduckgo", delay=0.01)
    b_wiki = MockSearchBackend("wikipedia", delay=0.01)
    backends = {"duckduckgo": b_ddg, "wikipedia": b_wiki}
    engine = SearchEngine(settings, backends=backends, cache=ResponseCache(tmp_path / "c"))

    resp = engine.search("quantum computing", options=SearchOptions(engines=("duckduckgo", "wikipedia")))
    assert resp.results
    engines_used = {r.engines[0] for r in resp.results}
    assert "bing" not in engines_used
    assert "duckduckgo" in engines_used or "wikipedia" in engines_used


def test_search_works_when_both_duckduckgo_and_bing_switched_off(tmp_path):
    # Only searxng and wikipedia configured
    settings = Settings(
        search_engines=("searxng", "wikipedia"),
        search_min_engines=1,
    )
    b_searx = MockSearchBackend("searxng", delay=0.01)
    b_wiki = MockSearchBackend("wikipedia", delay=0.01)
    backends = {"searxng": b_searx, "wikipedia": b_wiki}
    engine = SearchEngine(settings, backends=backends, cache=ResponseCache(tmp_path / "c"))

    resp = engine.search("astrophysics research", options=SearchOptions(engines=("searxng", "wikipedia")))
    assert resp.results
    engines_used = {r.engines[0] for r in resp.results}
    assert "duckduckgo" not in engines_used
    assert "bing" not in engines_used
    assert "searxng" in engines_used or "wikipedia" in engines_used
