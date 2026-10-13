"""Reliability primitives: classification, retries, circuit breaker, gate."""

import pytest

from iralens.errors import OperationTimeoutError, SourceUnavailableError
from iralens.reliability import (
    KIND_CAPTCHA, KIND_LAYOUT_CHANGED, KIND_RATE_LIMITED, KIND_TRANSIENT, KIND_UNAVAILABLE,
    CircuitBreaker, RetryPolicy, call_with_retries, classify_block, failure_kind, is_transient,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_classify_block_markers():
    assert classify_block("Please complete the CAPTCHA") == KIND_CAPTCHA
    assert classify_block("429 Too Many Requests") == KIND_RATE_LIMITED
    assert classify_block("a normal results page") is None


def test_failure_kind_mapping():
    assert failure_kind(SourceUnavailableError("x", detail="unusual traffic")) == KIND_CAPTCHA
    assert failure_kind(SourceUnavailableError("request timed out")) == KIND_TRANSIENT
    assert failure_kind(OperationTimeoutError("slow")) == KIND_TRANSIENT
    assert failure_kind(SourceUnavailableError("nope")) == KIND_UNAVAILABLE
    from iralens.errors import ExtractionError
    assert failure_kind(ExtractionError("x", detail="layout_changed")) == KIND_LAYOUT_CHANGED


def test_retry_policy_is_exponential_and_capped():
    p = RetryPolicy(max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=5.0)
    assert [p.delay(i) for i in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 5.0]


def test_call_with_retries_retries_only_transient_and_reports_attempts():
    delays = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationTimeoutError("timed out")
        return "ok"

    value, attempts = call_with_retries(flaky, RetryPolicy(3, 0.5, 4.0), sleep=delays.append)
    assert (value, attempts) == ("ok", 3)
    assert delays == [0.5, 1.0]


def test_call_with_retries_does_not_retry_non_transient():
    calls = {"n": 0}

    def blocked():
        calls["n"] += 1
        raise SourceUnavailableError("blocked", detail="captcha")

    with pytest.raises(SourceUnavailableError):
        call_with_retries(blocked, RetryPolicy(3, 0.0, 0.0), sleep=lambda s: None)
    assert calls["n"] == 1


def test_call_with_retries_gives_up_after_max_attempts():
    with pytest.raises(OperationTimeoutError):
        call_with_retries(lambda: (_ for _ in ()).throw(OperationTimeoutError("t")),
                          RetryPolicy(2, 0.0, 0.0), sleep=lambda s: None)


def test_breaker_opens_after_threshold_and_skips():
    clock = Clock()
    b = CircuitBreaker(threshold=2, cooldown=60, clock=clock)
    assert b.allow("bing")
    b.record_failure("bing")
    assert b.state("bing") == "closed"
    b.record_failure("bing")
    assert b.state("bing") == "open" and not b.allow("bing")


def test_breaker_success_resets_consecutive_count():
    b = CircuitBreaker(threshold=2, cooldown=60, clock=Clock())
    b.record_failure("e")
    b.record_success("e")
    b.record_failure("e")
    assert b.state("e") == "closed"


def test_breaker_half_open_allows_single_probe_then_closes_on_success():
    clock = Clock()
    b = CircuitBreaker(threshold=1, cooldown=60, clock=clock)
    b.record_failure("e")
    clock.now += 61
    assert b.state("e") == "half_open"
    assert b.allow("e")          # the probe
    assert not b.allow("e")      # no second probe while it is in flight
    b.record_success("e")
    assert b.state("e") == "closed" and b.allow("e")


def test_breaker_probe_failure_reopens():
    clock = Clock()
    b = CircuitBreaker(threshold=1, cooldown=60, clock=clock)
    b.record_failure("e")
    clock.now += 61
    assert b.allow("e")
    b.record_failure("e")
    assert b.state("e") == "open"


def test_is_transient_respects_message_content():
    assert is_transient(SourceUnavailableError("temporary failure"))
    assert not is_transient(SourceUnavailableError("bad input"))


def test_persistent_breaker_survives_reconstruction_and_claims_one_probe(tmp_path):
    from iralens.reliability import PersistentCircuitBreaker

    clock = Clock()
    path = tmp_path / "health.sqlite3"
    first = PersistentCircuitBreaker(2, 30, state_path=path, clock=clock)
    first.record_failure("duckduckgo", kind="unavailable")
    assert first.state("duckduckgo") == "closed"
    first.record_failure("duckduckgo", kind="unavailable")
    assert first.state("duckduckgo") == "open"

    restarted = PersistentCircuitBreaker(2, 30, state_path=path, clock=clock)
    assert restarted.state("duckduckgo") == "open"
    assert not restarted.allow("duckduckgo")
    clock.now += 31
    assert restarted.allow("duckduckgo")

    second_process = PersistentCircuitBreaker(2, 30, state_path=path, clock=clock)
    assert not second_process.allow("duckduckgo")
    restarted.record_success("duckduckgo", latency_seconds=0.25)
    assert second_process.state("duckduckgo") == "closed"
    assert second_process.allow("duckduckgo")


def test_persistent_breaker_reports_storage_failure_but_fails_open(tmp_path):
    from iralens.reliability import PersistentCircuitBreaker

    breaker = PersistentCircuitBreaker(2, 30, state_path=tmp_path)
    assert not breaker.storage_available()
    assert breaker.allow("search-engine")


def test_persistent_breaker_refuses_symlinked_state_directory(tmp_path):
    from iralens.reliability import PersistentCircuitBreaker

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")
    breaker = PersistentCircuitBreaker(2, 30, state_path=linked / "health.sqlite3")
    assert not breaker.storage_available()
    assert not (target / "health.sqlite3").exists()


def test_persistent_breaker_rate_limit_opens_immediately_and_backs_off(tmp_path):
    from iralens.reliability import KIND_RATE_LIMITED, PersistentCircuitBreaker

    clock = Clock()
    path = tmp_path / "health.sqlite3"
    breaker = PersistentCircuitBreaker(3, 30, blocked_cooldown=60,
                                       state_path=path, clock=clock)
    breaker.record_failure("duckduckgo", kind=KIND_RATE_LIMITED)
    assert breaker.state("duckduckgo") == "open"
    snapshot = breaker.snapshot()["duckduckgo"]
    assert snapshot["last_failure_kind"] == KIND_RATE_LIMITED
    assert snapshot["cooldown_remaining_seconds"] == 60
    clock.now += 61
    assert breaker.allow("duckduckgo")
    breaker.record_failure("duckduckgo", kind=KIND_RATE_LIMITED)
    assert breaker.snapshot()["duckduckgo"]["cooldown_remaining_seconds"] == 120
