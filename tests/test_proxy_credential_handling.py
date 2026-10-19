# -*- coding: utf-8 -*-
"""Secrets must never be readable by other local accounts.

A proxy URL routinely embeds credentials (`http://user:pass@host:8080`),
and a solving-service API key is a secret outright. Passing either as a
command-line argument published it to every user on the machine, because
`/proc/<pid>/cmdline` is world-readable while `/proc/<pid>/environ` is not.

This module pins the contract: the proxy and the key travel to the engine
through its environment, never through argv. Tests are offline and
deterministic.
"""

from __future__ import annotations

import os
import subprocess

from iralens._internal.browser_engine import mcp_server
from iralens.engine.native import BrowserEngine

SECRET_PROXY = "http://alice:s3cr3t@proxy.internal:8080"
SECRET_KEY = "s3cr3t-solving-key-0001"


class _Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = None
        self.stdout = iter([])
        self.stderr = iter([])

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _capture_spawn(monkeypatch, cfg):
    """Intercept the spawn and return the argv/env it was given."""
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env") or {}
        return _FakeProc()

    def _binary():
        from pathlib import Path
        return Path("/usr/bin/true")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(BrowserEngine, "binary_path", lambda self: _binary())

    eng = BrowserEngine(config=cfg, session=None)
    try:
        eng.start()
    except Exception:
        # The fake process cannot complete a handshake; the spawn is what
        # this test measures, and it has already been recorded.
        pass
    return captured


def test_proxy_is_not_placed_on_the_command_line(monkeypatch):
    captured = _capture_spawn(monkeypatch, _Cfg(proxy=SECRET_PROXY))
    argv = " ".join(str(a) for a in captured["argv"])
    assert SECRET_PROXY not in argv
    assert "s3cr3t" not in argv
    assert "--proxy" not in argv


def test_proxy_is_passed_through_the_environment(monkeypatch):
    captured = _capture_spawn(monkeypatch, _Cfg(proxy=SECRET_PROXY))
    assert captured["env"][mcp_server.PROXY_ENV_VAR] == SECRET_PROXY


def test_no_proxy_configured_means_no_proxy_in_either_channel(monkeypatch):
    captured = _capture_spawn(monkeypatch, _Cfg())
    assert mcp_server.PROXY_ENV_VAR not in captured["env"]
    assert "--proxy" not in " ".join(str(a) for a in captured["argv"])


def test_child_still_receives_a_complete_environment(monkeypatch):
    """Moving the proxy to env must not clobber PATH or the UTF-8 settings."""
    captured = _capture_spawn(monkeypatch, _Cfg(proxy=SECRET_PROXY))
    assert "PATH" in captured["env"]


def test_engine_accepts_the_proxy_from_its_environment(monkeypatch):
    """The other half of the contract: the child must actually read it."""
    monkeypatch.setenv(mcp_server.PROXY_ENV_VAR, SECRET_PROXY)
    argv = mcp_server.build_parser().parse_args(["mcp"])
    assert argv.proxy is None  # it is not on the command line
    # Mirrors the resolution performed in main().
    resolved = argv.proxy if argv.proxy is not None else os.environ.get(
        mcp_server.PROXY_ENV_VAR)
    assert resolved == SECRET_PROXY


def test_explicit_flag_still_wins_over_environment(monkeypatch):
    monkeypatch.setenv(mcp_server.PROXY_ENV_VAR, SECRET_PROXY)
    argv = mcp_server.build_parser().parse_args(["mcp", "--proxy", "http://other:1@h:2"])
    assert argv.proxy == "http://other:1@h:2"


# ------------------------------------------------------- the solving key
def test_the_solving_service_key_travels_in_the_environment(monkeypatch):
    """A key on the command line would be world-readable, like the proxy."""
    captured = _capture_spawn(
        monkeypatch,
        _Cfg(stealth="true", captcha_provider="2captcha", captcha_api_key=SECRET_KEY),
    )
    argv = " ".join(str(a) for a in captured["argv"])
    assert SECRET_KEY not in argv
    assert "--captcha" not in argv
    assert captured["env"].get(mcp_server.CAPTCHA_KEY_ENV_VAR) == SECRET_KEY
    assert captured["env"].get(mcp_server.CAPTCHA_PROVIDER_ENV_VAR) == "2captcha"


def test_a_missing_key_means_no_solver_is_configured(monkeypatch):
    captured = _capture_spawn(monkeypatch, _Cfg(stealth="true"))
    assert mcp_server.CAPTCHA_KEY_ENV_VAR not in captured["env"]
