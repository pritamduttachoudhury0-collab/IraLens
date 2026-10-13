# -*- coding: utf-8 -*-
"""Local HTTP API contract tests; no external network is used."""

import http.client
import json
import threading

import pytest

from iralens.api import APIService, create_server
from iralens.model import Artifact
from iralens.search.schema import EngineOutcome, RankedResult, SearchResponse


class FakeLens:
    def __init__(self):
        self.queries = []
        self.closed = False

    def search_api(self, query, filters=None, options=None):
        self.queries.append((query, filters, options))
        result = RankedResult(
            title="Solar result", url="https://example.org/solar",
            canonical_url="https://example.org/solar", snippet="A test snippet.",
            score=0.9, score_breakdown={"relevance": 0.9}, engines=["test"],
            engine_ranks={"test": 1}, published_at=None, date_status="unknown",
            queries=[query], security_flags=[],
        )
        return SearchResponse(
            query=query, results=[result], queries=[{"text": query, "strategy": "original"}],
            outcomes=[EngineOutcome(engine="test", status="results", kind="ok", count=1)],
            fallbacks=[], filters={"requested": filters or {}, "report": {}}, cache={},
            dedup_log=[], no_results_reason="none", summary="Found one test result.",
        )

    def open(self, url, mode="auto", max_chars=20000):
        return Artifact(title="Readable page", url=url, source="web", kind="page",
                        content="Local test body.", content_format="markdown",
                        retrieval_method="direct", metadata={"max_chars": max_chars})

    def close(self):
        self.closed = True


@pytest.fixture
def api_server():
    lens = FakeLens()
    service = APIService(lens)
    server = create_server("127.0.0.1", 0, api_key="test-api-token", service=service)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, lens
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        service.close()


def _request(server, method, path, *, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=encoded, headers=request_headers)
    response = conn.getresponse()
    status = response.status
    content_type = response.getheader("Content-Type", "")
    payload = response.read()
    conn.close()
    if "application/json" in content_type:
        payload = json.loads(payload.decode("utf-8"))
    else:
        payload = payload.decode("utf-8")
    return status, payload


def test_health_is_open_but_search_requires_service_auth(api_server):
    server, _lens = api_server
    status, health = _request(server, "GET", "/health")
    assert status == 200 and health["service"] == "iralens"
    status, error = _request(server, "GET", "/v1/search?q=solar")
    assert status == 401 and error["error"] == "unauthorized"


def test_serper_shaped_search_and_native_search_are_supported(api_server):
    server, lens = api_server
    status, compat = _request(
        server, "POST", "/search", body={"q": "solar", "num": 3},
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 200
    assert compat["organic"][0]["link"] == "https://example.org/solar"
    assert compat["searchParameters"]["q"] == "solar"
    status, nested_compat = _request(
        server, "POST", "/search", body={"q": "solar", "options": {"max_results": 2}},
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 200 and nested_compat["searchParameters"]["num"] == 2

    status, native = _request(
        server, "POST", "/v1/search",
        body={"query": "solar", "max_results": 4, "filters": {"language": "en"}},
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert status == 200 and native["results"][0]["title"] == "Solar result"
    assert lens.queries[-1][0] == "solar"
    assert lens.queries[-1][2]["max_results"] == 4
    assert lens.queries[-1][1]["language"] == "en"


def test_nested_search_options_and_filter_types_are_validated(api_server):
    server, lens = api_server
    status, _response = _request(
        server, "POST", "/v1/search",
        body={"query": "solar", "options": {"max_results": 2, "reformulate": False}},
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 200 and lens.queries[-1][2]["max_results"] == 2

    for body in (
        {"query": "solar", "max_results": 1.5},
        {"query": "solar", "filters": {"include_domains": "example.org"}},
        {"query": "solar", "options": {"reformulate": "false"}},
    ):
        status, error = _request(
            server, "POST", "/v1/search", body=body,
            headers={"X-API-KEY": "test-api-token"},
        )
        assert status == 400 and error["error"] == "invalid_input"


def test_read_route_and_request_validation(api_server):
    server, _lens = api_server
    status, page = _request(
        server, "POST", "/v1/read", body={"url": "https://example.org", "max_chars": 150},
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 200 and page["content"] == "Local test body."

    status, error = _request(
        server, "POST", "/v1/read", body={"url": "https://example.org", "max_char": 10},
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 400 and "unknown read option" in error["message"]

    status, error = _request(
        server, "POST", "/v1/search", body={"query": ""},
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 400 and error["error"] == "invalid_input"

    status, error = _request(
        server, "POST", "/v1/search", body={"query": "x"},
        headers={"X-API-KEY": "test-api-token", "Content-Type": "text/plain"},
    )
    assert status == 415 and error["error"] == "unsupported_media_type"


def test_unknown_search_options_are_rejected_and_health_exposes_circuits(api_server):
    server, _lens = api_server
    status, error = _request(
        server, "POST", "/v1/search", body={"query": "solar", "options": {"typo": True}},
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 400 and "unknown option" in error["message"]

    status, error = _request(server, "GET", "/v1/health/search")
    assert status == 401 and error["error"] == "unauthorized"
    status, health = _request(
        server, "GET", "/v1/health/search", headers={"X-API-KEY": "test-api-token"}
    )
    assert status == 200 and health["status"] == "unknown"


def test_metrics_are_prometheus_text_and_authenticated(api_server):
    server, _lens = api_server
    status, _ = _request(server, "GET", "/health")
    assert status == 200
    status, metrics = _request(
        server, "GET", "/metrics", headers={"Authorization": "Bearer test-api-token"}
    )
    assert status == 200
    assert "iralens_http_requests_total" in metrics
    assert "iralens_search_duration_seconds_count 0" in metrics
    status, _ = _request(
        server, "GET", "/attacker-controlled-path?query=private",
        headers={"X-API-KEY": "test-api-token"},
    )
    assert status == 404
    _, metrics = _request(
        server, "GET", "/metrics", headers={"Authorization": "Bearer test-api-token"}
    )
    assert 'route="other"' in metrics
    assert "/attacker-controlled-path" not in metrics and "private" not in metrics


def test_public_bind_requires_service_key(monkeypatch):
    monkeypatch.delenv("IRALENS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="IRALENS_API_KEY is required"):
        create_server("0.0.0.0", 0, service=APIService(FakeLens()))
    with pytest.raises(ValueError, match="at least 32 characters"):
        create_server("0.0.0.0", 0, api_key="too-short", service=APIService(FakeLens()))
    server = create_server(
        "0.0.0.0", 0,
        api_key="configured-service-token-with-at-least-32-characters",
        service=APIService(FakeLens()),
    )
    server.server_close()
