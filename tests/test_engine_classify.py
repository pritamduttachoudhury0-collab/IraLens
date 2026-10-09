# -*- coding: utf-8 -*-
"""Engine error classification — unified taxonomy, sanitized messages."""

from iralens.engine.native import classify_engine_error
from iralens.errors import (
    AuthRequiredError,
    ExtractionError,
    IraLensError,
    OperationTimeoutError,
    PageUnavailableError,
    SecurityBlockedError,
    SessionStateError,
)


def test_timeout():
    err = classify_engine_error("Error: Navigation timeout after 30s")
    assert isinstance(err, OperationTimeoutError)


def test_history_edges():
    assert isinstance(classify_engine_error("No previous page in history."), SessionStateError)
    assert isinstance(classify_engine_error("No forward page in history."), SessionStateError)
    assert isinstance(classify_engine_error("Nothing to reload."), SessionStateError)


def test_file_navigation_blocked():
    err = classify_engine_error("file:// navigation is disabled for MCP")
    assert isinstance(err, SecurityBlockedError)


def test_network_failure_maps_to_page_unavailable():
    err = classify_engine_error("Error: Network error: https://dead-host.test")
    assert isinstance(err, PageUnavailableError)
    # no duplicated Error: prefix in the user-facing message
    assert not err.message.startswith("Error:")


def test_selector_problems():
    assert isinstance(classify_engine_error("no element matches selector '#x'"), ExtractionError)
    assert isinstance(classify_engine_error("Missing 'ref' or 'selector' parameter"), ExtractionError)


def test_auth_walls():
    assert isinstance(classify_engine_error("403 Forbidden"), AuthRequiredError)
    assert isinstance(classify_engine_error("captcha challenge shown"), AuthRequiredError)


def test_unknown_tab():
    assert isinstance(classify_engine_error("unknown tab id 'zz'"), SessionStateError)


def test_messages_are_sanitized():
    err = classify_engine_error("obscura engine crashed with token=abc https://u:p@h.test")
    assert "obscura" not in (err.message + err.hint).lower()
    assert "abc" not in err.message
    assert "u:p@" not in err.message


def test_unknown_text_still_yields_base_error():
    err = classify_engine_error("something strange happened")
    assert isinstance(err, IraLensError)
    assert err.error_type == "error"
