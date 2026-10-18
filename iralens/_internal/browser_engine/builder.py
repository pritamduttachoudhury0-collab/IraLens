# -*- coding: utf-8 -*-
"""Build and installation helper for the vendored IraLens browser engine.

Supports two execution/packaging modes from the same vendored Obscura source tree
(`iralens/_internal/browser_engine/vendor/obscura`):
1. **Bundled Native V8 Mode (Default, Zero-Compile)**: Runs the vendored Obscura
   V8 runtime (`bootstrap.js`, `indexed_db.js`, `markdown.rs`) directly inside
   `py_mini_racer`'s prebuilt `libmini_racer` V8 isolate via `iralens-browser-engine`.
   Works immediately after `pip install iralens` with zero runtime network downloads
   and no Rust toolchain requirement.
2. **Rust Source Compilation Mode (Optional)**: When `cargo` is available and
   `prefer_rust_build=True` (or `IRALENS_BUILD_RUST_ENGINE=1`), compiles the
   vendored Rust crate `obscura-cli` from `vendor/obscura/Cargo.toml`.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .runtime import PACKAGE_DIR, VENDOR_OBSCURA_DIR

BUNDLED_BIN_DIR = PACKAGE_DIR / "bin"
BUNDLED_LAUNCHER_NAME = (
    "iralens-browser-engine.cmd" if sys.platform.startswith("win") else "iralens-browser-engine"
)

#: The launcher that ships *inside* the package. It is tracked package data and
#: is therefore **read-only at runtime**: setup, doctor and engine discovery
#: must never rewrite it. It stays relocatable by resolving its interpreter
#: when it runs, so it carries no build-machine or checkout path.
TRACKED_LAUNCHER_PATH = BUNDLED_BIN_DIR / BUNDLED_LAUNCHER_NAME

_RELOCATABLE_UNIX_LAUNCHER = (
    "#!/bin/sh\n"
    "# Launcher for the bundled IraLens native browser engine.\n"
    "#\n"
    "# This file is package data: it is intentionally relocatable and contains\n"
    "# no absolute paths. Do not regenerate it at runtime; the per-user,\n"
    "# machine-local launcher is written to the writable engine cache instead\n"
    "# (see ensure_generated_launcher).\n"
    "set -eu\n"
    'if [ -n "${IRALENS_PYTHON:-}" ] && [ -x "${IRALENS_PYTHON}" ]; then\n'
    '  exec "${IRALENS_PYTHON}" -m iralens._internal.browser_engine.mcp_server "$@"\n'
    "fi\n"
    "for py in python3 python; do\n"
    '  if command -v "$py" >/dev/null 2>&1; then\n'
    '    exec "$py" -m iralens._internal.browser_engine.mcp_server "$@"\n'
    "  fi\n"
    "done\n"
    'echo "iralens-browser-engine: no python interpreter found" >&2\n'
    "exit 127\n"
)

_RELOCATABLE_WINDOWS_LAUNCHER = (
    "@echo off\r\n"
    "rem Launcher for the bundled IraLens native browser engine.\r\n"
    "rem Package data: relocatable, no absolute paths, never regenerated at runtime.\r\n"
    'if defined IRALENS_PYTHON (\r\n'
    '  "%IRALENS_PYTHON%" -m iralens._internal.browser_engine.mcp_server %*\r\n'
    "  exit /b %ERRORLEVEL%\r\n"
    ")\r\n"
    "python -m iralens._internal.browser_engine.mcp_server %*\r\n"
)


def relocatable_launcher_content() -> str:
    """The tracked, machine-independent launcher body (used by packaging only)."""
    if sys.platform.startswith("win"):
        return _RELOCATABLE_WINDOWS_LAUNCHER
    return _RELOCATABLE_UNIX_LAUNCHER


def _render_unix_launcher(python_exe: Optional[str] = None) -> str:
    """A machine-local launcher bound to *python_exe*.

    This is written only to an explicitly writable runtime directory (the
    per-user engine cache or an install target), never to package data.
    """
    py = python_exe or sys.executable or "/usr/bin/env python3"
    return (
        "#!/bin/sh\n"
        "# Auto-generated launcher for the bundled IraLens native browser engine.\n"
        "# Machine-local: it names the interpreter that generated it. Regenerated\n"
        "# automatically when that interpreter changes; never written to the\n"
        "# installed package.\n"
        f'if [ -x "{py}" ]; then\n'
        f'  exec "{py}" -m iralens._internal.browser_engine.mcp_server "$@"\n'
        "fi\n"
        'echo "iralens-browser-engine: the interpreter that generated this launcher "\n'
        'echo "  is gone; run \'iralens install-engine\' or doctor to regenerate it." >&2\n'
        "exit 127\n"
    )


def _render_windows_launcher(python_exe: Optional[str] = None) -> str:
    py = python_exe or sys.executable or "python"
    return f'@echo off\r\n"{py}" -m iralens._internal.browser_engine.mcp_server %*\r\n'


def generated_launcher_content() -> str:
    """The body of a machine-local launcher for the running interpreter."""
    if sys.platform.startswith("win"):
        return _render_windows_launcher()
    return _render_unix_launcher()


def _write_launcher(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if not sys.platform.startswith("win"):
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def ensure_generated_launcher(target_dir: Path) -> Optional[Path]:
    """Ensure a runnable machine-local launcher exists in *target_dir*.

    *target_dir* must be an explicitly writable runtime location (the per-user
    engine cache or an install target). Nothing under the installed package is
    touched, so `git status` in a checkout stays clean and a read-only
    site-packages install keeps working.

    Returns ``None`` — never a stale path — when the launcher cannot be
    created or refreshed: a caller that receives ``None`` must report the
    engine as unavailable rather than execute an interpreter that no longer
    exists.
    """
    launcher = Path(target_dir) / BUNDLED_LAUNCHER_NAME
    content = generated_launcher_content()
    try:
        if launcher.is_file() and launcher.read_text(encoding="utf-8") == content:
            # Already current; just make sure it can still be executed.
            _ensure_executable(launcher)
            return launcher
    except OSError:
        pass
    try:
        return _write_launcher(launcher, content)
    except OSError:
        pass
    try:
        # An installed launcher can be mode 444 (or owned by another user in a
        # shared prefix). If we own it, make it writable and refresh it.
        if launcher.is_file():
            launcher.chmod(launcher.stat().st_mode | stat.S_IWUSR)
            return _write_launcher(launcher, content)
    except OSError:
        pass
    # Neither refreshable nor verifiable. Returning the existing file here is
    # exactly the old bug: it handed back a launcher pointing at an
    # interpreter that no longer exists. Report unavailable instead.
    return None


def _ensure_executable(path: Path) -> None:
    if sys.platform.startswith("win"):
        return
    try:
        mode = path.stat().st_mode
        if not (mode & stat.S_IXUSR):
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def ensure_bundled_launcher() -> Path:
    """Return the tracked in-package launcher **without modifying it at all**.

    Kept for backwards compatibility. Earlier revisions rewrote this file with
    the running interpreter's absolute path, which dirtied every checkout and
    baked a developer's checkout path into package data. The file is now
    relocatable package data: it is only ever read here (not even chmod-ed,
    so a checkout's tracked mode bit is preserved). Use
    :func:`ensure_generated_launcher` when a machine-local launcher is needed.
    """
    return TRACKED_LAUNCHER_PATH


def build_rust_vendor_binary(target_path: Path, *, timeout: int = 900) -> Optional[Path]:
    """Compile the vendored Rust workspace (`vendor/obscura`) if `cargo` is installed."""
    cargo_bin = shutil.which("cargo")
    if not cargo_bin or not (VENDOR_OBSCURA_DIR / "Cargo.toml").is_file():
        return None

    proc = subprocess.run(
        [cargo_bin, "build", "--release", "-p", "obscura-cli"],
        cwd=str(VENDOR_OBSCURA_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        return None

    exe_name = "obscura.exe" if sys.platform.startswith("win") else "obscura"
    built = VENDOR_OBSCURA_DIR / "target" / "release" / exe_name
    if not built.is_file():
        return None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, target_path)
    if not sys.platform.startswith("win"):
        mode = target_path.stat().st_mode
        target_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target_path


def install_bundled_engine(
    target_dir: Path,
    *,
    binary_name: str = "iralens-engine",
    prefer_rust_build: bool = False,
) -> Path:
    """Install the integrated browser engine launcher/binary into `target_dir`."""
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / binary_name

    if prefer_rust_build or os.environ.get("IRALENS_BUILD_RUST_ENGINE") == "1":
        built = build_rust_vendor_binary(dest)
        if built is not None:
            return built

    # *target_dir* is caller-supplied (the managed engine cache or an explicit
    # install target), so embedding the active interpreter here is correct and
    # stays out of the package.
    return _write_launcher(dest, generated_launcher_content())
