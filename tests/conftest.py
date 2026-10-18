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


def snapshot_source_singletons() -> list:
    """Remember the state of every module-level source object.

    `iralens.sources` keeps one instance per source, so a test that swaps an
    attribute on one (a fake engine, a stubbed reader) changes it for the
    whole session unless it is undone. It was undone by hand once and
    forgotten once; this is the safety net for every test that follows.
    """
    from iralens.sources import ALL_SOURCES

    return [(source, dict(vars(source))) for source in ALL_SOURCES]


def restore_source_singletons(snapshot: list) -> None:
    """Put every source object back exactly as `snapshot` found it."""
    for source, attributes in snapshot:
        for name in [n for n in vars(source) if n not in attributes]:
            delattr(source, name)
        for name, value in attributes.items():
            setattr(source, name, value)


class SourceRegistryGuard:
    """The state of the source registry, and the ability to put it back."""

    def __init__(self) -> None:
        self.snapshot = snapshot_source_singletons()

    def restore(self) -> None:
        restore_source_singletons(self.snapshot)


@pytest.fixture(autouse=True)
def pristine_sources():
    """Every test starts and ends with the source registry as it was."""
    guard = SourceRegistryGuard()
    yield guard
    guard.restore()


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
