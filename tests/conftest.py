# -*- coding: utf-8 -*-
import urllib.request
from pathlib import Path

import pytest

# Isolate config/session for every test session.
_TEST_HOME = Path("/tmp/iralens-test-home")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "iralens-home"
    home.mkdir()
    monkeypatch.setenv("IRALENS_HOME", str(home))
    # Keep engine PATH shims out of the real ~/.local/bin during tests.
    monkeypatch.setenv("IRALENS_BIN_DIR", str(home / "bin"))
    yield home


def _has_network() -> bool:
    """True only when a real HTTPS response comes back.

    A bare TCP connect is not enough: some networks can accept the TCP connection
    through a proxy while HTTPS fails, which made live tests run and fail.
    """
    try:
        with urllib.request.urlopen("https://example.com/", timeout=5) as resp:
            return 200 <= resp.status < 400
    except (OSError, ValueError):
        return False


def _has_engine() -> bool:
    from iralens.engine.locate import find_engine

    return find_engine() is not None


def cli_command() -> list:
    """The CLI invocation that belongs to *this* interpreter.

    Tests must never rely on a bare ``iralens`` being on PATH: the venv that
    runs pytest may not be activated, and a different (or missing) binary is
    then picked up — or nothing is. Prefer the console script installed next to
    the running interpreter; fall back to ``python -m iralens``, which is the
    same entry point (iralens/__main__.py).
    """
    import os
    import sys
    from pathlib import Path

    exe = Path(sys.executable)
    candidates = [exe.parent / "iralens"]
    if os.name == "nt":  # pragma: no cover - Windows is untested here
        candidates = [exe.parent / "iralens.exe", exe.parent / "iralens"]
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]
    return [sys.executable, "-m", "iralens"]


NETWORK = _has_network()
ENGINE = _has_engine()

live = pytest.mark.skipif(not NETWORK, reason="network unavailable")
browser = pytest.mark.skipif(
    not (NETWORK and ENGINE), reason="network or browser engine unavailable"
)
