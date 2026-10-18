# -*- coding: utf-8 -*-
"""Reliability primitives: failure classification, retries with backoff, and a
concurrency gate for the shared browser engine.

Used by the search subsystem (and available to any source). Every limit is
passed in from `settings.Settings`; nothing here reads global state.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple, TypeVar

from .config import state_dir

from .errors import IraLensError, OperationTimeoutError, SourceUnavailableError

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
    "unusual activity from your computer network",
)
#: Phrases that appear on a *rate-limit page*. A bare status code is not
#: evidence: the body of a page that merely *mentions* "429" (a reference
#: page, a changelog, a search result) is not itself rate-limited, and
#: treating it as such made a perfectly good one-hit SERP trigger a
#: needless browser navigation. Real status codes are handled structurally
#: by `classify_block(status=...)`.
_RATE_LIMIT_MARKERS = (
    "too many requests",
    "rate limit",
    "rate-limit",
    "ratelimited",
    "request limit exceeded",
    "quota exceeded",
    "access denied due to",
)
_TRANSIENT_MARKERS = (
    "timed out", "timeout", "temporar", "try again", "502", "503", "504",
    "connection reset", "lost connection",
)

#: Machine-generated detail markers: our own typed errors record the HTTP
#: status they were classified from, so this is a structural signal.
_HTTP_STATUS_RE = re.compile(r"\bhttp_(\d{3})\b")


def classify_block(text: str, *, status: Optional[int] = None) -> Optional[str]:
    """Classify a page or error text as a block signal, or None.

    `status` is the real HTTP status when the caller has one; it is
    authoritative and is never inferred from the body text.
    """
    if status == 429:
        return KIND_RATE_LIMITED
    low = (text or "").lower()
    if any(marker in low for marker in _CAPTCHA_MARKERS):
        return KIND_CAPTCHA
    if any(marker in low for marker in _RATE_LIMIT_MARKERS):
        return KIND_RATE_LIMITED
    # A typed error we produced ourselves records the status it came from.
    match = _HTTP_STATUS_RE.search(low)
    if match and match.group(1) == "429":
        return KIND_RATE_LIMITED
    return None


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, OperationTimeoutError):
        return True
    if isinstance(exc, (SourceUnavailableError, IraLensError)):
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

    def record_success(self, key: str, latency_seconds: Optional[float] = None) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)
            self._probing.discard(key)

    def record_failure(self, key: str, kind: Optional[str] = None) -> None:
        with self._lock:
            self._probing.discard(key)
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.threshold or key in self._opened_at:
                self._opened_at[key] = self._clock()


class PersistentCircuitBreaker(CircuitBreaker):
    """SQLite-backed circuit breaker shared across threads and process restarts.

    The in-memory :class:`CircuitBreaker` remains available for callers that
    need an isolated breaker. Search uses this durable variant so a CAPTCHA,
    rate limit, or repeated outage is not immediately re-tried by every new
    CLI process. A short SQLite transaction claims the half-open probe across
    processes; a broken or read-only state store fails open rather than taking
    search down.

    Only engine/source identifiers and health counters are persisted. Search
    queries, result text, API keys, and page content are never stored here.
    """

    def __init__(
        self,
        threshold: int,
        cooldown: float,
        *,
        state_path: Optional[Path] = None,
        blocked_cooldown: Optional[float] = None,
        max_cooldown: float = 21600.0,
        probe_lease: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # Reuse the public threshold/cooldown attributes and lock, but all
        # state transitions below are committed transactionally to SQLite.
        super().__init__(threshold, cooldown, clock=clock)
        self.path = Path(state_path) if state_path else state_dir() / "search-health.sqlite3"
        self.blocked_cooldown = max(float(cooldown), float(blocked_cooldown or cooldown))
        self.max_cooldown = max(float(max_cooldown), self.blocked_cooldown)
        self.probe_lease = max(1.0, float(probe_lease))
        self._db_lock = threading.RLock()
        self._prepare_database()

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink() or self.path.parent.is_symlink() or self.path.parent.parent.is_symlink():
            raise sqlite3.OperationalError("search health state path must not traverse symlinks")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.path.parent.is_symlink() or self.path.parent.parent.is_symlink():
            raise sqlite3.OperationalError("search health state path changed to a symlink")
        if os.name != "nt":
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        conn = sqlite3.connect(str(self.path), timeout=3.0)
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._db_lock:
            conn = self._connect()
            try:
                yield conn
            finally:
                conn.close()

    def _prepare_database(self) -> None:
        try:
            with self._connection() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS engine_circuit ("
                    "key TEXT PRIMARY KEY, consecutive_failures INTEGER NOT NULL DEFAULT 0, "
                    "total_failures INTEGER NOT NULL DEFAULT 0, total_successes INTEGER NOT NULL DEFAULT 0, "
                    "cooldown_until REAL NOT NULL DEFAULT 0, probe_until REAL NOT NULL DEFAULT 0, "
                    "last_failure_kind TEXT NOT NULL DEFAULT '', last_failure_at REAL NOT NULL DEFAULT 0, "
                    "ewma_latency REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)"
                )
                conn.commit()
            if os.name != "nt" and self.path.exists():
                self.path.chmod(0o600)
        except (OSError, sqlite3.Error):
            # Search should still attempt its sources if local persistence is
            # unavailable (e.g. a read-only home directory).
            return

    def state(self, key: str) -> str:
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT cooldown_until, probe_until FROM engine_circuit WHERE key=?", (key,)
                ).fetchone()
        except (OSError, sqlite3.Error):
            return "closed"
        if row is None or not row[0]:
            return "closed"
        now = self._clock()
        if now < float(row[0]):
            return "open"
        if float(row[1]) > now:
            return "half_open"
        return "half_open"

    def allow(self, key: str) -> bool:
        now = self._clock()
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT cooldown_until, probe_until FROM engine_circuit WHERE key=?", (key,)
                ).fetchone()
                if row is None or not float(row[0]):
                    conn.commit()
                    return True
                cooldown_until, probe_until = float(row[0]), float(row[1])
                if now < cooldown_until or now < probe_until:
                    conn.commit()
                    return False
                conn.execute(
                    "UPDATE engine_circuit SET probe_until=?, updated_at=? WHERE key=?",
                    (now + self.probe_lease, now, key),
                )
                conn.commit()
                return True
        except (OSError, sqlite3.Error):
            return True

    def record_success(self, key: str, latency_seconds: Optional[float] = None) -> None:
        now = self._clock()
        latency = max(0.0, float(latency_seconds or 0.0))
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO engine_circuit "
                    "(key, consecutive_failures, total_failures, total_successes, cooldown_until, "
                    "probe_until, last_failure_kind, last_failure_at, ewma_latency, updated_at) "
                    "VALUES (?, 0, 0, 1, 0, 0, '', 0, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET consecutive_failures=0, "
                    "total_successes=total_successes+1, cooldown_until=0, probe_until=0, "
                    "ewma_latency=CASE WHEN excluded.ewma_latency=0 THEN ewma_latency "
                    "WHEN ewma_latency=0 THEN excluded.ewma_latency "
                    "ELSE 0.25*excluded.ewma_latency+0.75*ewma_latency END, updated_at=excluded.updated_at",
                    (key, latency, now),
                )
                conn.commit()
        except (OSError, sqlite3.Error):
            return

    def record_failure(self, key: str, kind: Optional[str] = None) -> None:
        now = self._clock()
        failure = (kind or KIND_UNAVAILABLE).strip().lower()
        blocked = failure in (KIND_CAPTCHA, KIND_RATE_LIMITED)
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT consecutive_failures, cooldown_until FROM engine_circuit WHERE key=?",
                    (key,),
                ).fetchone()
                streak = (int(row[0]) if row else 0) + 1
                was_open = bool(row and float(row[1]) > 0)
                delay = 0.0
                if blocked or streak >= self.threshold or was_open:
                    exponent = max(0, streak - 1) if blocked else max(0, streak - self.threshold)
                    base = self.blocked_cooldown if blocked else float(self.cooldown)
                    delay = min(self.max_cooldown, base * (2 ** min(exponent, 10)))
                conn.execute(
                    "INSERT INTO engine_circuit "
                    "(key, consecutive_failures, total_failures, total_successes, cooldown_until, "
                    "probe_until, last_failure_kind, last_failure_at, ewma_latency, updated_at) "
                    "VALUES (?, ?, 1, 0, ?, 0, ?, ?, 0, ?) "
                    "ON CONFLICT(key) DO UPDATE SET consecutive_failures=excluded.consecutive_failures, "
                    "total_failures=total_failures+1, cooldown_until=excluded.cooldown_until, "
                    "probe_until=0, last_failure_kind=excluded.last_failure_kind, "
                    "last_failure_at=excluded.last_failure_at, updated_at=excluded.updated_at",
                    (key, streak, now + delay if delay else 0.0, failure, now, now),
                )
                conn.commit()
        except (OSError, sqlite3.Error):
            return

    def storage_available(self) -> bool:
        """Probe the local state store; failures still fail open for searches."""
        try:
            with self._connection() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except (OSError, sqlite3.Error):
            return False

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        """Health counters for operational diagnostics; never includes query data."""
        now = self._clock()
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT key, consecutive_failures, total_failures, total_successes, "
                    "cooldown_until, last_failure_kind, last_failure_at, ewma_latency "
                    "FROM engine_circuit ORDER BY key"
                ).fetchall()
        except (OSError, sqlite3.Error):
            return {}
        result: Dict[str, Dict[str, object]] = {}
        for row in rows:
            cooldown_until = float(row[4])
            state = "closed" if not cooldown_until else ("open" if now < cooldown_until else "half_open")
            result[str(row[0])] = {
                "state": state,
                "consecutive_failures": int(row[1]),
                "total_failures": int(row[2]),
                "total_successes": int(row[3]),
                "cooldown_remaining_seconds": max(0.0, cooldown_until - now),
                "last_failure_kind": str(row[5]),
                "last_failure_at": float(row[6]),
                "ewma_latency_seconds": float(row[7]),
            }
        return result


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


class HostRateLimiter:
    """A polite *rate* budget per host — pacing, not just concurrency.

    `PerSiteConcurrencyGate` bounds how many requests are in flight at
    once, but three simultaneous requests are still three requests in the
    same instant, and that is what earns a 429 or a captcha. This limiter
    additionally enforces a minimum interval *between* requests to the
    same host, and it honours the origin's `Retry-After` when one is
    given: after a 429 the next request to that host waits for the
    interval the origin asked for.
    """

    def __init__(self, min_interval: float = 0.25, default_min_interval: float = 0.25,
                 max_cooldown: float = 60.0) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.default_min_interval = max(0.0, float(default_min_interval))
        self.max_cooldown = max(0.0, float(max_cooldown))
        self._intervals: Dict[str, float] = {}        # per-host minimum gap
        self._last_request: Dict[str, float] = {}    # per-host last request time
        self._cooldown_until: Dict[str, float] = {}  # per-host Retry-After deadline
        self._lock = threading.Lock()

    def interval_for(self, host: str) -> float:
        """The minimum gap this limiter will keep for *host*."""
        with self._lock:
            return max(self.min_interval,
                       self._intervals.get(host, self.default_min_interval))

    def set_interval(self, host: str, seconds: float) -> None:
        """Set a per-host minimum gap."""
        try:
            value = max(0.0, float(seconds))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._intervals[host] = value

    def note_retry_after(self, host: str, seconds: float) -> None:
        """Remember an origin's `Retry-After` so the next request waits."""
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        value = min(value, self.max_cooldown)
        with self._lock:
            deadline = time.monotonic() + value
            self._cooldown_until[host] = max(self._cooldown_until.get(host, 0.0), deadline)

    def cooldown_remaining(self, host: str) -> float:
        """Seconds until *host* may be contacted again (0 when free)."""
        now = time.monotonic()
        with self._lock:
            return max(0.0, self._cooldown_until.get(host, 0.0) - now)

    def wait(self, host: str, *, sleep: Optional[Callable[[float], None]] = None) -> float:
        """Block until *host* may be contacted again. Returns the slept time."""
        now = time.monotonic()
        with self._lock:
            gap = max(self.min_interval,
                      self._intervals.get(host, self.default_min_interval))
            cooldown = self._cooldown_until.get(host, 0.0)
            last = self._last_request.get(host, 0.0)
            delay = max(0.0, cooldown - now, last + gap - now)
        if delay > 0:
            (sleep or time.sleep)(delay)
        with self._lock:
            self._last_request[host] = time.monotonic()
        return delay


#: Process-wide polite rate budget. Shared so that a search burst and a
#: page-read burst against the same host cannot add up.
HOST_RATE_LIMITER = HostRateLimiter()


class TransportHealthMemory:
    """Which transport last worked for a host.

    `mode=auto` has two paths: a static read and a rendered one. When the
    rendered path is dead for a host — the browser engine is not installed,
    or the page crashes the renderer — every read of that host pays for the
    failed render and still gets the same static text. This memory lets the
    reader converge: it remembers, per host, that the renderer does not
    work there, and says so instead of trying again and again.

    The memory is a *hint*, never a hard rule: a transport that starts
    working again is picked up immediately.
    """

    def __init__(self, failure_threshold: int = 2, ttl_seconds: float = 900.0) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _entry(self, host: str) -> Dict[str, Any]:
        return self._state.setdefault(
            str(host or "").lower(),
            {"ok": 0, "fail": 0, "last_ok": 0.0, "last_fail": 0.0},
        )

    def record(self, host: str, transport: str, *, ok: bool) -> None:
        """Note that *transport* did (or did not) work for *host*."""
        key = f"{str(host or '').lower()}|{transport}"
        with self._lock:
            entry = self._entry(key)
            if ok:
                entry["ok"] += 1
                entry["fail"] = 0
                entry["last_ok"] = time.monotonic()
            else:
                entry["fail"] += 1
                entry["last_fail"] = time.monotonic()
                entry["last_ok"] = 0.0

    def is_dead(self, host: str, transport: str) -> bool:
        """Whether *transport* has failed repeatedly for *host*."""
        key = f"{str(host or '').lower()}|{transport}"
        with self._lock:
            entry = self._state.get(key)
            if not entry:
                return False
            if self.ttl_seconds and (time.monotonic() - entry["last_fail"]) > self.ttl_seconds:
                return False
            return entry["fail"] >= self.failure_threshold

    def known_good(self, host: str, transport: str) -> bool:
        """Whether *transport* has worked for *host* at least once."""
        key = f"{str(host or '').lower()}|{transport}"
        with self._lock:
            entry = self._state.get(key)
            return bool(entry and entry["ok"] > 0)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._state.items()}


#: Process-wide transport memory, shared by every read.
TRANSPORT_HEALTH = TransportHealthMemory()


class PerSiteConcurrencyGate:
    """Polite concurrency gate per target host or engine name.

    Ensures that while overall search concurrency can be high (e.g. 20+),
    any single upstream search provider or site is accessed with polite
    bounded concurrency (e.g. 2-3 simultaneous connections) to prevent
    rate-limiting, bot challenges, or provider degradation.
    """

    def __init__(self, limit: int = 3, acquire_timeout: float = 15.0) -> None:
        self.limit = max(1, limit)
        self.acquire_timeout = acquire_timeout
        self._sems: Dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()

    def _sem(self, key: str) -> threading.BoundedSemaphore:
        with self._lock:
            if key not in self._sems:
                self._sems[key] = threading.BoundedSemaphore(self.limit)
            return self._sems[key]

    @contextmanager
    def guard(self, key: str, timeout: Optional[float] = None) -> Iterator[None]:
        sem = self._sem(key)
        to = self.acquire_timeout if timeout is None else timeout
        if not sem.acquire(timeout=to):
            raise OperationTimeoutError(
                f"too many concurrent requests to {key}; retry shortly",
                hint=f"polite per-site limit is {self.limit}",
            )
        try:
            yield
        finally:
            sem.release()
