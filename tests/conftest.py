# -*- coding: utf-8 -*-
import urllib.request
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
    """True only when a real HTTPS response comes back.

    A bare TCP connect is not enough: sandboxes can accept the TCP connection
    through a proxy while HTTPS fails, which made live tests run and fail.
    """
    try:
        with urllib.request.urlopen("https://example.com/", timeout=5) as resp:
            return 200 <= resp.status < 400
    except (OSError, ValueError):
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
