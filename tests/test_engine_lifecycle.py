# -*- coding: utf-8 -*-
"""Engine lifecycle regression tests (offline).

Root cause fixed here: the browser-engine driver waited out the full RPC
timeout (60 s) whenever the engine process had *already died*. A dangling
`iralens-engine` shim (cache cleared, binary replaced, wrong architecture)
therefore cost 60 s per browser-backed call — 181 s for a single search and
12 minutes for a three-query benchmark smoke run in a blocked sandbox.

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
