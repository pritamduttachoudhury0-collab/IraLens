# -*- coding: utf-8 -*-
"""Engine lifecycle regression tests (offline).

Root cause fixed here: the browser-engine driver waited out the full RPC
timeout (60 s) whenever the engine process had *already died*. A dangling
`iralens-engine` shim (cache cleared, binary replaced, wrong architecture)
therefore cost 60 s per browser-backed call — 181 s for a single search and
12 minutes for a three-query benchmark smoke run on a blocked network.

Everything below uses real subprocesses running tiny scripts, so the polling,
the typed error, and the no-respawn memo are exercised for real, with no
network and no engine binary.
"""

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from iralens import errors
from iralens.engine import native
from iralens.engine.native import BrowserEngine


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _engine(tmp_path: Path, monkeypatch, name: str, body: str) -> BrowserEngine:
    binary = _script(tmp_path, name, body)
    monkeypatch.setattr(native, "find_engine", lambda config=None: binary)
    return BrowserEngine()


def test_dead_engine_fails_fast_with_a_typed_error(tmp_path, monkeypatch):
    """A binary that exits immediately must not cost the full RPC timeout."""
    engine = _engine(tmp_path, monkeypatch, "dead-engine",
                     "import sys\nsys.stderr.write('cannot load shared library\\n')\n"
                     "sys.exit(3)\n")
    started = time.monotonic()
    with pytest.raises(errors.EngineUnavailableError) as exc_info:
        engine.call("browser_navigate", {"url": "https://example.com"}, timeout=30)
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"a dead engine took {elapsed:.1f}s to be reported"
    assert exc_info.value.error_type == "browser_engine_unavailable"
    assert "exit 3" in str(exc_info.value.detail)
    assert "shared library" in str(exc_info.value.detail)
    assert "install-engine" in str(exc_info.value.hint)


def test_dead_engine_is_not_respawned_on_every_call(tmp_path, monkeypatch):
    """The failure is remembered: three calls must not spawn three processes."""
    counter = tmp_path / "spawns.txt"
    engine = _engine(
        tmp_path, monkeypatch, "counted-engine",
        "import sys, pathlib\n"
        f"p = pathlib.Path({str(counter)!r})\n"
        "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
        "sys.exit(1)\n")
    for _ in range(3):
        with pytest.raises(errors.EngineUnavailableError):
            engine.call("browser_navigate", {"url": "https://example.com"}, timeout=20)
    assert counter.read_text() == "x", "the broken engine was respawned per call"


def test_hung_engine_honours_the_callers_timeout(tmp_path, monkeypatch):
    """A live engine that answers the handshake but hangs on a tool call is
    bounded by the caller's timeout (no false "exited" either)."""
    body = (
        "import json, sys, time\n"
        "line = sys.stdin.readline()\n"
        "req = json.loads(line)\n"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
        " 'result': {'protocolVersion': '2024-11-05'}}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )
    engine = _engine(tmp_path, monkeypatch, "hung-engine", body)
    started = time.monotonic()
    with pytest.raises(errors.OperationTimeoutError) as exc_info:
        engine.call("browser_navigate", {"url": "https://example.com"}, timeout=2)
    elapsed = time.monotonic() - started
    assert 1.5 < elapsed < 15, elapsed
    assert "did not respond within 2s" in str(exc_info.value)
    engine.close(persist_state=False)


def test_hung_startup_is_bounded_and_configurable(tmp_path, monkeypatch):
    """A binary that never answers `initialize` is bounded by the handshake
    budget (20 s by default, overridable — the test uses 1 s)."""
    from iralens.config import Config

    engine = _engine(tmp_path, monkeypatch, "never-speaks",
                     "import time\ntime.sleep(120)\n")
    engine.config = Config()
    monkeypatch.setenv("IRALENS_ENGINE_STARTUP_TIMEOUT_SECONDS", "1")
    started = time.monotonic()
    with pytest.raises(errors.OperationTimeoutError) as exc_info:
        engine.call("browser_navigate", {"url": "https://example.com"}, timeout=30)
    elapsed = time.monotonic() - started
    assert elapsed < 10, elapsed
    assert "did not respond within 1s" in str(exc_info.value)
    engine.close(persist_state=False)


def test_engine_that_dies_after_initialize_fails_fast(tmp_path, monkeypatch):
    """The engine answers the handshake, then dies — the next call must notice."""
    body = (
        "import json, sys\n"
        "line = sys.stdin.readline()\n"
        "req = json.loads(line)\n"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
        " 'result': {'protocolVersion': '2024-11-05'}}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "sys.exit(9)\n"
    )
    engine = _engine(tmp_path, monkeypatch, "one-shot-engine", body)
    started = time.monotonic()
    with pytest.raises(errors.EngineUnavailableError) as exc_info:
        engine.call("browser_navigate", {"url": "https://example.com"}, timeout=30)
    elapsed = time.monotonic() - started
    assert elapsed < 10, elapsed
    assert "exited" in str(exc_info.value.message)
    assert "exit 9" in str(exc_info.value.detail)


def test_engine_failure_text_is_sanitized(tmp_path, monkeypatch):
    """Internal component names from the engine's stderr never reach the caller."""
    engine = _engine(tmp_path, monkeypatch, "leaky-engine",
                     "import sys\nsys.stderr.write('obscura: agent-reach backend crashed\\n')\n"
                     "sys.exit(2)\n")
    with pytest.raises(errors.EngineUnavailableError) as exc_info:
        engine.call("browser_navigate", {"url": "https://example.com"}, timeout=20)
    blob = json.dumps(exc_info.value.to_dict()).lower()
    for term in ("obscura", "agent-reach", "agent_reach"):
        assert term not in blob, f"implementation ancestry leaked: {term}"


def test_missing_engine_binary_is_typed(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "find_engine", lambda config=None: None)
    engine = BrowserEngine()
    with pytest.raises(errors.EngineUnavailableError) as exc_info:
        engine.call("browser_navigate", {"url": "https://example.com"})
    assert "not installed" in str(exc_info.value)
    assert exc_info.value.error_type == "browser_engine_unavailable"


def test_working_engine_round_trip(tmp_path, monkeypatch):
    """A well-behaved engine still works: the polling loop must not break it."""
    body = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('method') == 'initialize':\n"
        "        result = {'protocolVersion': '2024-11-05'}\n"
        "    else:\n"
        "        result = {'content': [{'type': 'text', 'text': json.dumps({'ok': True})}],"
        " 'isError': False}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
        " 'result': result}) + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    engine = _engine(tmp_path, monkeypatch, "good-engine", body)
    assert engine.call("browser_navigate", {"url": "https://example.com"}, timeout=10) == {
        "ok": True}
    assert engine.running
    engine.close(persist_state=False)
    assert not engine.running


def test_concurrent_start_spawns_exactly_one_child(tmp_path, monkeypatch):
    """Two threads racing on start() must not spawn two engines (one orphan).

    Root cause fixed: start() checked `running` and spawned without a lock, so
    concurrent callers (the search pool runs browser reads in parallel) could
    both observe "not running" and each Popen a child. The lifecycle lock now
    serializes check-and-spawn-and-handshake.
    """
    import threading

    counter = tmp_path / "spawns.txt"
    body = (
        "import json, os, sys\n"
        f"with open({str(counter)!r}, 'a') as f:\n"
        "    f.write(str(os.getpid()) + '\\n')\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('method') == 'initialize':\n"
        "        result = {'protocolVersion': '2024-11-05'}\n"
        "    else:\n"
        "        result = {'content': [{'type': 'text', 'text': json.dumps({'ok': True})}],\n"
        "                  'isError': False}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],\n"
        "                                 'result': result}) + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    engine = _engine(tmp_path, monkeypatch, "race-engine", body)
    barrier = threading.Barrier(8)
    errors_seen: list = []

    def racer():
        try:
            barrier.wait(timeout=10)
            engine.start()
        except Exception as exc:  # pragma: no cover - surfaced below
            errors_seen.append(exc)

    threads = [threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors_seen, errors_seen
    assert engine.running
    spawns = counter.read_text().strip().splitlines() if counter.exists() else []
    assert len(spawns) == 1, f"expected exactly one engine spawn, got {len(spawns)}: {spawns}"
    engine.close(persist_state=False)
    assert not engine.running


def test_terminate_reaps_a_killed_child_no_zombie(tmp_path, monkeypatch):
    """A child that ignores stdin-close is killed AND waited on (no zombie).

    Root cause fixed: the failure path called kill() without a following
    wait(), leaving a zombie process-table entry until the parent exited.
    """
    body = (
        "import json, sys, time\n"
        "line = sys.stdin.readline()\n"
        "req = json.loads(line)\n"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],\n"
        "                             'result': {'protocolVersion': '2024-11-05'}}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n"  # ignores stdin close; must be killed and reaped
    )
    engine = _engine(tmp_path, monkeypatch, "stubborn-engine", body)
    engine.start()
    assert engine.running
    proc = engine._proc
    assert proc is not None
    engine._terminate()
    # wait() must have reaped the child: a returncode means it is not a zombie.
    assert proc.returncode is not None, "killed child was not reaped (zombie)"
    assert engine._proc is None


def test_close_kills_then_waits_when_child_will_not_exit(tmp_path, monkeypatch):
    """close() on a hung child must kill AND reap it, not leave a zombie."""
    body = (
        "import json, sys, time\n"
        "line = sys.stdin.readline()\n"
        "req = json.loads(line)\n"
        "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],\n"
        "                             'result': {'protocolVersion': '2024-11-05'}}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )
    engine = _engine(tmp_path, monkeypatch, "hung-close-engine", body)
    engine.start()
    proc = engine._proc
    assert proc is not None
    # persist_state=False avoids a storage_state RPC; browser_close will time
    # out (the child is asleep), then stdin-close + wait time out, then kill.
    engine.close(persist_state=False)
    assert proc.returncode is not None, "close() left a zombie (kill without wait)"
    assert engine._proc is None
    assert not engine.running


def test_env_override_binary_is_used(tmp_path):
    """IRALENS_ENGINE_PATH wins over discovery (documented behavior)."""
    binary = _script(tmp_path, "explicit-engine",
                     "import json, sys\n"
                     "for line in sys.stdin:\n"
                     "    req = json.loads(line)\n"
                     "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
                     " 'result': {'protocolVersion': '2024-11-05'}}) + '\\n')\n"
                     "    sys.stdout.flush()\n")
    os.environ["IRALENS_ENGINE_PATH"] = str(binary)
    try:
        from iralens.engine.locate import find_engine

        assert find_engine() == binary
    finally:
        os.environ.pop("IRALENS_ENGINE_PATH", None)
