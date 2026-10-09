# -*- coding: utf-8 -*-
"""Unified data model + error taxonomy tests."""

import json

from iralens.errors import (
    AuthRequiredError,
    EngineUnavailableError,
    ExtractionError,
    IraLensError,
    NavigationError,
    OperationTimeoutError,
    OperationUnsupportedError,
    PageUnavailableError,
    SecurityBlockedError,
    SessionStateError,
    SourceUnavailableError,
    classify_error,
)
from iralens.model import Artifact, SearchResult


def test_artifact_roundtrip():
    a = Artifact(
        title="T", url="https://x.test", source="github", kind="repo",
        content="body", content_format="markdown",
        metadata={"stars": 5}, retrieval_method="api:github",
        discovered_from="search:q",
    )
    d = json.loads(a.to_json())
    assert d["title"] == "T"
    assert d["metadata"]["stars"] == 5
    assert d["discovered_from"] == "search:q"
    assert d["untrusted"] is True
    assert d["retrieved_at"].endswith("Z")
    assert "obscura" not in a.to_json().lower()
    assert "agent" not in d["retrieval_method"]


def test_search_result_to_artifact_provenance():
    r = SearchResult(title="t", url="https://y.test", snippet="s", rank=2)
    a = r.to_artifact("my query", "search:browser-search")
    assert a.source == "web-search"
    assert a.kind == "search_result"
    assert a.discovered_from == "search:my query"
    assert a.metadata["rank"] == 2
    assert a.metadata["snippet"] == "s"


def test_error_taxonomy_types():
    cases = {
        PageUnavailableError: "page_unavailable",
        NavigationError: "navigation_failed",
        SourceUnavailableError: "source_unavailable",
        AuthRequiredError: "authentication_required",
        OperationUnsupportedError: "operation_unsupported",
        OperationTimeoutError: "timeout",
        ExtractionError: "extraction_failed",
        SecurityBlockedError: "blocked_by_security_policy",
        EngineUnavailableError: "browser_engine_unavailable",
        SessionStateError: "session_state_error",
    }
    for cls, name in cases.items():
        err = cls("m", hint="h")
        assert isinstance(err, IraLensError)
        assert err.error_type == name
        assert err.to_dict() == {"error": name, "message": "m", "hint": "h"}


def test_classify_error_roundtrip():
    for name in ("timeout", "authentication_required", "page_unavailable"):
        err = classify_error(name, "msg")
        assert err.to_dict()["error"] == name
    # unknown machine names fall back to the base error type
    assert classify_error("nope", "msg").to_dict()["error"] == "error"
