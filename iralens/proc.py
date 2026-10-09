# -*- coding: utf-8 -*-
"""Subprocess execution and honest health probing.

Adapted from the capability-layer source's probing framework: `shutil.which()`
alone is not proof of health (stale venv shims pass `which` but cannot exec),
so probes actually execute a lightweight command and classify
missing / broken / timeout / error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from .errors import OperationTimeoutError, SourceUnavailableError
from .security import public_message

UTF8_ENV = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

_BROKEN_EXIT_CODES = (126, 127)


def utf8_env(base: Optional[Mapping[str, str]] = None, extra: Optional[Mapping[str, str]] = None) -> dict:
    env = dict(base or os.environ)
    env.update(UTF8_ENV)
    if extra:
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


@dataclass
class ProbeResult:
    status: str  # ok | missing | broken | timeout | error
    output: str = ""
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def reinstall_hint(package: str) -> str:
    return (
        f"'{package}' exists on PATH but cannot execute (often a stale virtualenv "
        f"after a Python upgrade). Reinstall it: pipx reinstall {package}"
    )


def probe_command(
    cmd: str,
    args: Sequence[str] = ("--version",),
    timeout: int = 10,
    package: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    remove_env: Sequence[str] = (),
) -> ProbeResult:
    """Execute `cmd *args` and classify the result. Side-effect-free probes only."""
    resolved = shutil.which(cmd)
    if not resolved:
        return ProbeResult("missing", hint=f"'{cmd}' not found on PATH")

    child_env = dict(env or os.environ)
    child_env.update(UTF8_ENV)
    for key in remove_env:
        child_env.pop(key, None)

    try:
        proc = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
    except FileNotFoundError:
        return ProbeResult("broken", hint=reinstall_hint(package or cmd))
    except subprocess.TimeoutExpired:
        return ProbeResult("timeout", hint=f"'{cmd}' did not respond within {timeout}s")
    except OSError as exc:
        return ProbeResult("broken", hint=public_message(exc))

    if proc.returncode in _BROKEN_EXIT_CODES:
        return ProbeResult("broken", hint=reinstall_hint(package or cmd))
    if proc.returncode != 0:
        output = (proc.stderr or proc.stdout or "").strip()
        return ProbeResult("error", output=output, hint=f"'{cmd}' exited {proc.returncode}")
    return ProbeResult("ok", output=(proc.stdout or "").strip())


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_cli(
    argv: Sequence[str],
    *,
    timeout: int = 60,
    env_extra: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
    stdin_text: Optional[str] = None,
) -> CommandResult:
    """Run an external CLI, raising unified errors for missing/broken tools."""
    cmd = argv[0]
    resolved = shutil.which(cmd)
    if not resolved:
        raise SourceUnavailableError(
            f"required tool '{cmd}' is not installed",
            hint=f"install '{cmd}' or configure an alternative backend",
        )
    try:
        proc = subprocess.run(
            [resolved, *[str(a) for a in argv[1:]]],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=utf8_env(extra=env_extra),
            cwd=cwd,
            input=stdin_text,
        )
    except FileNotFoundError as exc:
        raise SourceUnavailableError(f"tool '{cmd}' cannot execute", hint=reinstall_hint(cmd)) from exc
    except subprocess.TimeoutExpired as exc:
        raise OperationTimeoutError(f"'{cmd}' timed out after {timeout}s") from exc
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def run_cli_json(
    argv: Sequence[str],
    *,
    timeout: int = 60,
    env_extra: Optional[Mapping[str, str]] = None,
    json_loads: Any = json.loads,
) -> Any:
    """Run a CLI expected to emit JSON on stdout."""
    result = run_cli(argv, timeout=timeout, env_extra=env_extra)
    if not result.ok:
        raise SourceUnavailableError(
            public_message((result.stderr or result.stdout or f"'{argv[0]}' failed").strip()[:500]),
            detail=f"exit={result.returncode}",
        )
    try:
        return json_loads(result.stdout)
    except Exception as exc:
        raise SourceUnavailableError(
            f"'{argv[0]}' returned output that is not valid JSON"
        ) from exc
