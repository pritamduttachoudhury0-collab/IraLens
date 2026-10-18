# -*- coding: utf-8 -*-
"""Subprocess execution and honest health probing.

Adapted from the capability-layer source's probing framework: `shutil.which()`
alone is not proof of health (stale venv shims pass `which` but cannot exec),
so probes actually execute a lightweight command and classify
missing / broken / timeout / error.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import OperationTimeoutError, SourceUnavailableError
from .security import public_message

UTF8_ENV = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

_BROKEN_EXIT_CODES = (126, 127)

#: Console scripts that can also be run as ``python -m <module>`` when their
#: launcher is missing from PATH. Keyed by the on-disk executable name.
_MODULE_FALLBACKS: Dict[str, str] = {
    "yt-dlp": "yt_dlp",
}


def interpreter_bin_dirs() -> List[Path]:
    """Executable directories that belong to the *running* interpreter.

    ``shutil.which()`` only sees ``PATH``, and IraLens is commonly launched
    through a virtualenv's absolute entry point (as ``scripts/setup.sh`` does)
    without activating that environment. A tool installed into the same
    environment then looks absent even though this process can run it. The
    interpreter's own ``bin``/``Scripts`` directory is checked first, then
    ``prefix`` and ``base_prefix`` (so a venv can fall back to its base).
    """
    directories: List[Path] = []
    seen = set()
    if sys.executable:
        try:
            directories.append(Path(sys.executable).resolve().parent)
        except OSError:
            pass
    for base in (getattr(sys, "prefix", None), getattr(sys, "exec_prefix", None),
                 getattr(sys, "base_prefix", None)):
        if not base:
            continue
        candidate = Path(base) / ("Scripts" if sys.platform.startswith("win") else "bin")
        if str(candidate) not in seen:
            seen.add(str(candidate))
            directories.append(candidate)
    return [d for d in directories if str(d) not in ("", ".")]


def resolve_executable(cmd: str) -> Optional[str]:
    """Locate *cmd* on ``PATH`` or in the running interpreter's environment.

    Returns ``None`` when the command cannot be found at all. ``PATH`` wins,
    so an operator's explicit choice is never overridden.
    """
    found = shutil.which(cmd)
    if found:
        return found
    names = [str(cmd)]
    if sys.platform.startswith("win"):
        names += [f"{cmd}.exe", f"{cmd}.cmd", f"{cmd}.bat"]
    for directory in interpreter_bin_dirs():
        for name in names:
            candidate = directory / name
            try:
                if candidate.is_file() and os.access(str(candidate), os.X_OK):
                    return str(candidate)
            except OSError:
                continue
    return None


def module_invocation(cmd: str) -> Optional[List[str]]:
    """``[python, '-m', module]`` for *cmd*, when it has a usable module form.

    A wheel can ship a console script that a restricted or relocated install
    did not create; the module entry point still works and belongs to the same
    interpreter, so it is the most faithful fallback.
    """
    module = _MODULE_FALLBACKS.get(str(cmd))
    if not module or not sys.executable:
        return None
    try:
        if importlib.util.find_spec(module) is None:
            return None
    except (ImportError, ValueError):
        return None
    return [sys.executable, "-m", module]


def command_argv(cmd: str, args: Sequence[str] = ()) -> List[str]:
    """The full argv for *cmd*: resolved executable, or a module invocation."""
    resolved = resolve_executable(cmd)
    if resolved:
        return [resolved, *args]
    via_module = module_invocation(cmd)
    if via_module:
        return [*via_module, *args]
    return [str(cmd), *args]


def is_command_available(cmd: str) -> bool:
    """Whether *cmd* can be run by this process (PATH, venv, or module form)."""
    return resolve_executable(cmd) is not None or module_invocation(cmd) is not None


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
    """Execute `cmd *args` and classify the result. Side-effect-free probes only.

    The command is resolved through :func:`command_argv`, so a tool installed
    into the running interpreter's environment is found even when that
    environment is not on ``PATH``.
    """
    resolved = resolve_executable(cmd)
    if resolved:
        argv = [resolved, *args]
    else:
        via_module = module_invocation(cmd)
        if via_module is None:
            return ProbeResult("missing", hint=f"'{cmd}' not found on PATH")
        argv = [*via_module, *args]

    child_env = dict(env or os.environ)
    child_env.update(UTF8_ENV)
    for key in remove_env:
        child_env.pop(key, None)

    try:
        proc = subprocess.run(
            argv,
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
    if not is_command_available(cmd):
        raise SourceUnavailableError(
            f"required tool '{cmd}' is not installed",
            hint=f"install '{cmd}' or configure an alternative backend",
        )
    try:
        proc = subprocess.run(
            command_argv(cmd, [str(a) for a in argv[1:]]),
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
