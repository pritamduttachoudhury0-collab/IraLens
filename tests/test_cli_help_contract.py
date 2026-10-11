# -*- coding: utf-8 -*-
"""CLI invocation contract (offline regression tests).

These lock in three Step-0 fixes:

1. `iralens --help` purity is checked against the CLI of *this* interpreter
   (the venv's console script, else `python -m iralens`) — never a bare
   `iralens` from PATH, which failed wherever the venv was not activated.
2. `python -m iralens` is a real entry point with identical exit codes.
3. The default static read path is reported as `direct` (D-077), which the
   live Test A now accepts alongside `static-reader` and `browser`.
"""

import json
import subprocess
import sys
from pathlib import Path

from iralens import cli

from .conftest import cli_command


def _run(argv, timeout=60):
    return subprocess.run(cli_command() + argv, capture_output=True, text=True, timeout=timeout)


def test_cli_command_resolves_to_this_environment():
    """The helper must not depend on PATH: something real must be runnable."""
    command = cli_command()
    assert command, "no CLI command resolved"
    if command[0] != sys.executable:
        assert Path(command[0]).is_file(), f"{command[0]} is not a real file"
        assert Path(command[0]).parent == Path(sys.executable).parent
    else:
        assert command[1:] == ["-m", "iralens"]


def test_help_is_pure_and_exits_zero():
    """Regression: bare `iralens` in a non-activated venv raised FileNotFoundError."""
    out = _run(["--help"])
    assert out.returncode == cli.EXIT_OK, (out.stdout, out.stderr)
    assert "usage:" in out.stdout.lower()
    blob = (out.stdout + out.stderr).lower()
    for term in ("obscura", "agent-reach", "agent_reach", "agentreach"):
        assert term not in blob, f"implementation ancestry leaked into --help: {term}"


def test_module_entry_point_matches_the_console_script():
    """`python -m iralens` and the console script are one implementation."""
    direct = _run(["--version"])
    module = subprocess.run([sys.executable, "-m", "iralens", "--version"],
                            capture_output=True, text=True, timeout=60)
    assert direct.returncode == cli.EXIT_OK
    assert module.returncode == cli.EXIT_OK
    assert module.stdout.strip() == direct.stdout.strip()
    assert "iralens" in direct.stdout


def test_module_entry_point_reports_caller_errors_the_same_way():
    """Exit codes are part of the D-079 contract, whichever entry point is used."""
    bad = subprocess.run([sys.executable, "-m", "iralens", "--json", "search-api", ""],
                         capture_output=True, text=True, timeout=60)
    assert bad.returncode == cli.EXIT_CALLER
    payload = json.loads(bad.stderr)
    assert payload["error"] == "invalid_input"


def test_every_help_page_is_pure():
    """Subcommand help must be as clean as the top-level help."""
    blob = ""
    for name in ("search-api", "read", "open", "install-engine", "doctor"):
        out = _run([name, "--help"])
        assert out.returncode == cli.EXIT_OK, (name, out.stderr)
        blob += out.stdout + out.stderr
    blob = blob.lower()
    for term in ("obscura", "agent-reach", "agent_reach"):
        assert term not in blob, f"implementation ancestry leaked into subcommand help: {term}"
