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


def test_json_flag_works_before_and_after_subcommand():
    from halfiralens.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["--json", "research", "q"]).json is True
    assert parser.parse_args(["research", "q", "--json"]).json is True
    assert parser.parse_args(["research", "q"]).json is False
    assert parser.parse_args(["search-api", "q", "--json", "--engine", "bing"]).json is True


def test_json_output_serializes_artifacts_as_objects(capsys):
    """Regression: `fetch ... --json` printed Artifact reprs as strings, which
    agents could not parse. Artifacts must come out as JSON objects."""
    import json as _json

    from halfiralens.cli import _out
    from halfiralens.model import Artifact

    art = Artifact(title="owner/repo", url="https://github.com/owner/repo", source="github",
                   content="A headless browser", kind="repo", metadata={"stars": 3})
    _out([art], as_json=True)
    captured = capsys.readouterr()
    data = _json.loads(captured.out)
    assert isinstance(data, list) and data[0]["url"] == "https://github.com/owner/repo"
    assert data[0]["metadata"]["stars"] == 3
    assert "Artifact(" not in captured.out
    from halfiralens.security import UNTRUSTED_NOTICE
    assert UNTRUSTED_NOTICE in captured.err      # the notice goes to stderr
    assert UNTRUSTED_NOTICE not in captured.out  # stdout stays pure JSON


@pytest.mark.parametrize("bad", ["../evil", "a/../../evil", "/abs/evil", "..\\evil", "C:evil", ""])
def test_engine_archive_rejects_unsafe_member_names(bad):
    """Regression for the bandit High finding (zip extraction): unsafe member
    names must be refused before extraction, not left to zipfile's defaults."""
    from halfiralens.engine.install import check_archive_member
    from halfiralens.errors import EngineUnavailableError

    with pytest.raises(EngineUnavailableError):
        check_archive_member(bad)


@pytest.mark.parametrize("good", ["obscura", "bin/obscura", "obscura.exe", "dir/sub/file.txt"])
def test_engine_archive_accepts_normal_member_names(good):
    from halfiralens.engine.install import check_archive_member

    check_archive_member(good)  # must not raise
