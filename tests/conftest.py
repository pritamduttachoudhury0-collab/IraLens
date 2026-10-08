# -*- coding: utf-8 -*-
import socket
from pathlib import Path

import pytest

# Isolate config/session for every test session.
_TEST_HOME = Path("/tmp/half-iralens-test-home")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "hil-home"
    home.mkdir()
    monkeypatch.setenv("HALF_IRALENS_HOME", str(home))
    yield home


def _has_network() -> bool:
    try:
        socket.create_connection(("example.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


def _has_engine() -> bool:
    from halfiralens.engine.locate import find_engine

    return find_engine() is not None


NETWORK = _has_network()
ENGINE = _has_engine()

live = pytest.mark.skipif(not NETWORK, reason="network unavailable")
browser = pytest.mark.skipif(
    not (NETWORK and ENGINE), reason="network or browser engine unavailable"
)
