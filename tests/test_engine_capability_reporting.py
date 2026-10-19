# -*- coding: utf-8 -*-
"""Optional features must be discoverable, never silently assumed.

`--stealth` was once declared on the CLI and forwarded to the engine
subprocess while no code read it, so a caller setting `stealth: true` believed
requests were protected when nothing had changed. The contract that replaced
that is the one tested here: the engine advertises what it actually
implements, a request is reported together with whether it was *applied*, and
a requested-but-unavailable feature is surfaced rather than dropped.

Stealth is now implemented by the Python/native-V8 tier (`stealth.py`), so
"requested" and "applied" agree in the shipped configuration; the unavailable
branch is still exercised below with a simulated engine, because a future
tier may again be unable to honour the request.
"""

from __future__ import annotations

import json
import subprocess
import sys

from iralens._internal.browser_engine import mcp_server
from iralens.engine.native import BrowserEngine


# --------------------------------------------------------------- payload unit
def test_stealth_is_advertised_as_supported():
    assert mcp_server.ENGINE_CAPABILITIES["stealth"]["supported"] is True


def test_supported_capabilities_are_advertised_as_supported():
    assert mcp_server.ENGINE_CAPABILITIES["javascript_rendering"]["supported"] is True


def test_unrequested_stealth_reports_requested_false():
    payload = mcp_server.capabilities_payload()
    assert payload["stealth"]["requested"] is False
    assert payload["stealth"]["applied"] is False


def test_requested_stealth_is_applied_and_reported():
    """The caller must be able to distinguish 'off' from 'on' and from 'no'."""
    payload = mcp_server.capabilities_payload(stealth_requested=True)
    assert payload["stealth"]["requested"] is True
    assert payload["stealth"]["applied"] is True
    assert payload["stealth"]["supported"] is True


def test_unavailable_stealth_is_reported_as_not_applied():
    """A tier that cannot honour the request must say so, not fake success."""
    payload = mcp_server.capabilities_payload(
        stealth_requested=True,
        engine_stealth={"applied": False, "reason": "no stealth implementation"},
    )
    assert payload["stealth"]["requested"] is True
    assert payload["stealth"]["applied"] is False
    assert "reason" in payload["stealth"]


def test_payload_is_a_copy_not_the_shared_constant():
    """Mutating one response must not corrupt later handshakes."""
    first = mcp_server.capabilities_payload(stealth_requested=True)
    first["stealth"]["requested"] = "mutated"
    second = mcp_server.capabilities_payload()
    assert second["stealth"]["requested"] is False
    assert mcp_server.ENGINE_CAPABILITIES["stealth"].get("requested") is None


def test_stealth_notice_states_what_it_does_and_does_not_do():
    """Marketing restraint is part of the contract, not an aside."""
    notice = mcp_server._stealth_notice().lower()
    assert "stealth active" in notice
    assert "captcha" in notice
    assert "tls" in notice


# ------------------------------------------------------------ end-to-end CLI
def _handshake(extra_args):
    """Run the real engine over stdio and return (stdout, stderr)."""
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1"}},
    }) + "\n"
    argv = [sys.executable, "-m", "iralens._internal.browser_engine.mcp_server",
            "mcp"] + list(extra_args)
    proc = subprocess.run(argv, input=request, capture_output=True, text=True,
                          timeout=120)
    return proc.stdout, proc.stderr


def test_engine_reports_capabilities_in_handshake():
    out, _ = _handshake([])
    result = json.loads(out.strip().splitlines()[0])["result"]
    assert "engineCapabilities" in result
    assert result["engineCapabilities"]["javascript_rendering"]["supported"] is True


def test_requesting_stealth_is_reported_not_swallowed():
    """The regression: the flag used to vanish with no trace anywhere."""
    out, err = _handshake(["--stealth"])
    result = json.loads(out.strip().splitlines()[0])["result"]
    stealth = result["engineCapabilities"]["stealth"]
    assert stealth["requested"] is True
    assert stealth["applied"] is True
    assert stealth["supported"] is True
    # The applied identity travels with the handshake, so a client can check
    # what it is actually wearing without reading the engine source.
    assert stealth["profile"]["user_agent"].startswith("Mozilla/5.0")
    assert stealth["profile"]["sec_ch_ua"]
    # And it is said out loud, so an operator watching stderr sees it too.
    assert "stealth" in err.lower()


def test_engine_still_starts_with_the_flag():
    """An optional feature must never take the browser tier down."""
    out, _ = _handshake(["--stealth"])
    assert json.loads(out.strip().splitlines()[0])["result"]["serverInfo"]["name"]


def test_no_flag_means_no_warning():
    _, err = _handshake([])
    assert "stealth" not in err.lower()


# ------------------------------------------------------------------- driver
class _Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_driver_records_what_the_engine_reports():
    eng = BrowserEngine(config=_Cfg(), session=None)
    eng._record_capabilities({
        "engineCapabilities": {
            "stealth": {"supported": False, "requested": True, "applied": False,
                        "reason": "not compiled"},
        }
    })
    assert eng.capabilities["stealth"]["applied"] is False
    assert eng.capability_warnings
    assert "not active" in eng.capability_warnings[0]


def test_driver_stays_quiet_when_stealth_was_never_asked_for():
    eng = BrowserEngine(config=_Cfg(), session=None)
    eng._record_capabilities({
        "engineCapabilities": {
            "stealth": {"supported": False, "requested": False, "applied": False},
        }
    })
    assert eng.capability_warnings == []


def test_driver_tolerates_an_engine_without_capability_reporting():
    """Older/other engines must not break the handshake."""
    eng = BrowserEngine(config=_Cfg(), session=None)
    eng._record_capabilities({"protocolVersion": "2024-11-05"})
    assert eng.capabilities == {}
    status = eng.stealth_status()
    assert status["active"] is False
    assert status["supported"] is None


def test_stealth_status_is_machine_readable():
    eng = BrowserEngine(config=_Cfg(stealth="true"), session=None)
    eng._record_capabilities({
        "engineCapabilities": {
            "stealth": {"supported": True, "requested": True, "applied": True,
                        "profile": {"user_agent": "x"}},
        }
    })
    status = eng.stealth_status()
    assert set(status) >= {"requested", "supported", "active", "note"}
    assert status["requested"] is True
    assert status["supported"] is True
    assert status["active"] is True
    assert eng.capability_warnings == []


def test_health_surfaces_the_warning_when_running(monkeypatch):
    eng = BrowserEngine(config=_Cfg(stealth="true"), session=None)
    eng.capability_warnings.append("stealth was requested but is not active")
    monkeypatch.setattr(type(eng), "running", property(lambda self: True))
    health = eng.health()
    assert health["status"] == "ok"
    assert health.get("warnings")
