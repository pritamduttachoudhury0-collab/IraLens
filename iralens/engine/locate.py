# -*- coding: utf-8 -*-
"""Locate the native browser engine executable.

Search order:
1. Explicit path passed via ``Config.engine_path``.
2. ``IRALENS_ENGINE_PATH`` environment variable.
3. Managed cache directory: ``~/.cache/iralens/engine/`` (or ``$XDG_CACHE_HOME/iralens/engine/``).
4. ``PATH`` (looking for ``iralens-browser-engine``, ``iralens-engine``, or ``obscura``).
5. A launcher **generated** into the managed cache directory (step 3). This is
   the machine-local, writable location; nothing inside the installed package
   is ever written at runtime.
6. Bundled in-package engine at ``iralens/_internal/browser_engine/bin/iralens-browser-engine``
   (always available after standard package installation without runtime downloads).

Nothing in this module writes to package data. The in-package launcher (step 6)
is relocatable and carries no absolute path, so it is safe to ship and safe to
keep: setup, doctor and discovery leave a checkout's tracked files untouched.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .._internal.browser_engine.builder import (
    TRACKED_LAUNCHER_PATH,
    ensure_generated_launcher,
)

ENGINE_BINARY_NAMES: List[str] = (
    ["iralens-browser-engine.cmd", "iralens-engine.exe", "obscura.exe"]
    if sys.platform.startswith("win")
    else ["iralens-browser-engine", "iralens-engine", "obscura"]
)


def engine_cache_dir() -> Path:
    """Return the directory where the managed engine binary is stored."""
    override = os.environ.get("IRALENS_ENGINE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "iralens" / "engine"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "iralens" / "engine"
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "iralens" / "engine"
    return Path.home() / ".cache" / "iralens" / "engine"


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def bundled_engine_path() -> Optional[Path]:
    """Return the bundled in-package browser engine executable.

    Read-only: the launcher is tracked package data and is never generated,
    rewritten or chmod-ed here, so running doctor from a checkout cannot dirty
    it. It resolves its own interpreter at run time, so it works from any
    location and cannot carry a stale absolute path.
    """
    if os.environ.get("IRALENS_DISABLE_BUNDLED_ENGINE") == "1":
        return None
    try:
        if _is_executable(TRACKED_LAUNCHER_PATH):
            return TRACKED_LAUNCHER_PATH
    except Exception:
        return None
    return None


def generated_engine_path(target_dir: Optional[Path] = None) -> Optional[Path]:
    """A machine-local launcher generated into a *writable* runtime directory.

    This is where an interpreter-specific launcher belongs: it is per-user,
    per-machine, and outside the installed package. Returns ``None`` when the
    directory cannot be written and no usable launcher is already there, so a
    caller reports the engine as unavailable instead of exec'ing a launcher
    that points at a deleted interpreter.
    """
    if os.environ.get("IRALENS_DISABLE_BUNDLED_ENGINE") == "1":
        return None
    try:
        path = ensure_generated_launcher(target_dir or engine_cache_dir())
    except Exception:
        return None
    if path is None:
        return None
    return path if _is_executable(path) else None


def find_engine(config=None) -> Optional[Path]:
    """Return the path to a working native browser engine binary, or ``None``."""
    # 1. Config override
    cfg_path = getattr(config, "engine_path", None) if config is not None else None
    if cfg_path:
        p = Path(cfg_path).expanduser()
        if _is_executable(p):
            return p

    # 2. Environment variable
    env_path = os.environ.get("IRALENS_ENGINE_PATH")
    if env_path:
        p = Path(env_path).expanduser()
        if _is_executable(p):
            return p

    # 3. Managed cache directory
    cache = engine_cache_dir()
    for name in ENGINE_BINARY_NAMES:
        candidate = cache / name
        if _is_executable(candidate):
            return candidate

    # 4. System PATH
    for name in ENGINE_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            p = Path(found)
            if _is_executable(p):
                return p

    # 5. A launcher generated into the writable per-user cache. Machine-local
    #    by design: this is the only place an absolute interpreter path is
    #    ever written, and it is never package data.
    generated = generated_engine_path(cache)
    if generated is not None:
        return generated

    # 6. Bundled native browser engine shipped inside
    #    iralens._internal.browser_engine (relocatable, read-only package data).
    return bundled_engine_path()
