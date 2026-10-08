# -*- coding: utf-8 -*-
"""Release-hardening regression tests (offline)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from halfiralens import __version__
from halfiralens.mcp_server import MCPServer

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def server():
    srv = MCPServer()
    yield srv
    srv.hil.close()


def _call(server, name, arguments):
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}})
    return reply["result"], json.loads(reply["result"]["content"][0]["text"])


def test_missing_required_argument_is_invalid_input(server):
    result, body = _call(server, "search_api", {})
    assert result["isError"] is True
    assert body["error"] == "invalid_input"
    assert "query" in body["message"]


def test_malformed_filter_is_invalid_input_not_internal_error(server):
    result, body = _call(server, "search_api", {"query": "x", "filters": {"date_from": "2025-99-01"}})
    assert result["isError"] is True
    assert body["error"] == "invalid_input"


def test_unknown_tool_is_reported_not_crashed(server):
    result, body = _call(server, "no_such_tool", {})
    assert result["isError"] is True


def test_cli_version_flag_prints_package_version():
    out = subprocess.run([sys.executable, "-m", "halfiralens.cli", "--version"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert __version__ in out.stdout


def test_setup_scripts_are_present_and_posix_script_parses():
    sh = ROOT / "scripts" / "setup.sh"
    ps1 = ROOT / "scripts" / "setup.ps1"
    assert sh.is_file() and ps1.is_file()
    check = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert "UNTESTED" in ps1.read_text(encoding="utf-8")
