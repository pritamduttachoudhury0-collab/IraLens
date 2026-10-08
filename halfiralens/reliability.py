# -*- coding: utf-8 -*-
"""Reliability primitives: failure classification, retries with backoff, and a
concurrency gate for the shared browser engine.

Used by the search subsystem (and available to any source). Every limit is
passed in from `settings.Settings`; nothing here reads global state.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, TypeVar

from .errors import HalfIraLensError, OperationTimeoutError, SourceUnavailableError

T = TypeVar("T")

#: Failure kinds shared by the search fallback chain and the research layer.
KIND_OK = "ok"
KIND_EMPTY = "empty"
KIND_CAPTCHA = "captcha"
KIND_RATE_LIMITED = "rate_limited"
KIND_LAYOUT_CHANGED = "layout_changed"
KIND_TRANSIENT = "transient"
KIND_UNAVAILABLE = "unavailable"

_CAPTCHA_MARKERS = (
    "captcha", "unusual traffic", "are you a robot", "verify you are human",
    "anomaly-modal", "challenge-platform", "confirm you're not a robot",
)
_RATE_LIMIT_MARKERS = ("too many requests", "rate limit", "rate-limit", "429")
_TRANSIENT_MARKERS = (
    "timed out", "timeout", "temporar", "try again", "502", "503", "504",
    "connection reset", "lost connection",
)


def classify_block(text: str) -> Optional[str]:
    """Classify a page or error text as a block signal, or None."""
    low = (text or "").lower()
    if any(marker in low for marker in _CAPTCHA_MARKERS):
        return KIND_CAPTCHA
    if any(marker in low for marker in _RATE_LIMIT_MARKERS):
        return KIND_RATE_LIMITED
    return None


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, OperationTimeoutError):
        return True
    if isinstance(exc, (SourceUnavailableError, HalfIraLensError)):
        low = f"{exc.message} {exc.detail}".lower()
        return any(marker in low for marker in _TRANSIENT_MARKERS)
    return False


def failure_kind(exc: BaseException) -> str:
    """Map an exception to a failure kind used by the fallback chain."""
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'detail', '')} {exc}"
    block = classify_block(text)
    if block:
        return block
    if "layout_changed" in text:
        return KIND_LAYOUT_CHANGED
    if is_transient(exc):
        return KIND_TRANSIENT
    return KIND_UNAVAILABLE


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0

    def delay(self, attempt: int) -> float:
        """Delay before retry number `attempt` (1-based): exponential, capped."""
        return min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))


def call_with_retries(
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    should_retry: Callable[[BaseException], bool] = is_transient,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[T, int]:
    """Run fn, retrying transient failures with backoff. Returns (value, attempts)."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(), attempt
        except Exception as exc:  # noqa: BLE001 - classification happens below
            if attempt >= max(1, policy.max_attempts) or not should_retry(exc):
                raise
            sleep(policy.delay(attempt))


class CircuitBreaker:
    """Skip an engine after repeated consecutive failures; probe again after a cooldown.

    States per key: closed (normal) -> open after `threshold` consecutive failures
    -> half_open once `cooldown` seconds pass (one probe allowed) -> closed on
    success, or back to open on failure. Thread-safe. `clock` is injectable so
    tests do not sleep.
    """

    def __init__(self, threshold: int, cooldown: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.threshold = max(1, int(threshold))
        self.cooldown = float(cooldown)
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict = {}
        self._opened_at: dict = {}
        self._probing: set = set()

    def state(self, key: str) -> str:
        with self._lock:
            return self._state_locked(key)

    def _state_locked(self, key: str) -> str:
        opened = self._opened_at.get(key)
        if opened is None:
            return "closed"
        if self._clock() - opened >= self.cooldown:
            return "half_open"
        return "open"

    def allow(self, key: str) -> bool:
        with self._lock:
            state = self._state_locked(key)
            if state == "closed":
                return True
            if state == "half_open" and key not in self._probing:
                self._probing.add(key)   # exactly one probe at a time
                return True
            return False

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)
            self._probing.discard(key)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._probing.discard(key)
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.threshold or key in self._opened_at:
                self._opened_at[key] = self._clock()


class ConcurrencyGate:
    """Bounded concurrency for the shared browser engine with timeouts.

    Use as a context manager; the slot is always released, even when the
    guarded block raises.
    """

    def __init__(self, limit: int, acquire_timeout: float) -> None:
        self._sem = threading.BoundedSemaphore(max(1, limit))
        self._timeout = acquire_timeout
        self.limit = max(1, limit)

    def __enter__(self) -> "ConcurrencyGate":
        if not self._sem.acquire(timeout=self._timeout):
            raise OperationTimeoutError(
                "too many concurrent searches; retry shortly",
                hint=f"concurrency limit is {self.limit}",
            )
        return self

    def __exit__(self, *exc_info) -> None:
        self._sem.release()
