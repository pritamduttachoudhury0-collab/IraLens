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


def _render_unix_launcher(python_exe: Optional[str] = None) -> str:
    py = python_exe or sys.executable or "/usr/bin/env python3"
    repo_root = PACKAGE_DIR.parent.parent.parent
    return (
        "#!/bin/sh\n"
        "# Auto-generated launcher for the bundled IraLens native browser engine.\n"
        f'if [ -x "{py}" ]; then\n'
        f'  exec "{py}" -m iralens._internal.browser_engine.mcp_server "$@"\n'
        "fi\n"
        f'PYTHONPATH="{repo_root}:${{PYTHONPATH:-}}" exec python3 -m iralens._internal.browser_engine.mcp_server "$@"\n'
    )


def _render_windows_launcher(python_exe: Optional[str] = None) -> str:
    py = python_exe or sys.executable or "python"
    return f'@echo off\r\n"{py}" -m iralens._internal.browser_engine.mcp_server %*\r\n'


def ensure_bundled_launcher() -> Path:
    """Ensure the in-package `bin/iralens-browser-engine` executable exists and is runnable."""
    BUNDLED_BIN_DIR.mkdir(parents=True, exist_ok=True)
    launcher = BUNDLED_BIN_DIR / BUNDLED_LAUNCHER_NAME

    if sys.platform.startswith("win"):
        content = _render_windows_launcher()
    else:
        content = _render_unix_launcher()

    needs_write = True
    if launcher.is_file():
        try:
            existing = launcher.read_text(encoding="utf-8")
            if existing == content:
                needs_write = False
        except OSError:
            needs_write = True

    if needs_write:
        try:
            launcher.write_text(content, encoding="utf-8")
            if not sys.platform.startswith("win"):
                mode = launcher.stat().st_mode
                launcher.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            # Read-only site-packages fallback: if file already exists, return it
            if launcher.is_file():
                return launcher
            raise

    if not sys.platform.startswith("win"):
        try:
            mode = launcher.stat().st_mode
            if not (mode & stat.S_IXUSR):
                launcher.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass

    return launcher


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

    content = (
        _render_windows_launcher()
        if sys.platform.startswith("win")
        else _render_unix_launcher()
    )
    dest.write_text(content, encoding="utf-8")
    if not sys.platform.startswith("win"):
        mode = dest.stat().st_mode
        dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest
