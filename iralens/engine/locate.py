# -*- coding: utf-8 -*-
"""Locate the native browser engine executable.

Search order:
1. Explicit path passed via ``Config.engine_path``.
2. ``IRALENS_ENGINE_PATH`` environment variable.
3. Managed cache directory: ``~/.cache/iralens/engine/`` (or ``$XDG_CACHE_HOME/iralens/engine/``).
4. ``PATH`` (looking for ``iralens-browser-engine``, ``iralens-engine``, or ``obscura``).
5. Bundled in-package engine at ``iralens/_internal/browser_engine/bin/iralens-browser-engine``
   (always available after standard package installation without runtime downloads).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .._internal.browser_engine.builder import ensure_bundled_launcher

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
    """Return the bundled in-package browser engine executable."""
    if os.environ.get("IRALENS_DISABLE_BUNDLED_ENGINE") == "1":
        return None
    try:
        launcher = ensure_bundled_launcher()
        if _is_executable(launcher):
            return launcher
    except Exception:
        return None
    return None


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

    # 5. Bundled native browser engine shipped inside iralens._internal.browser_engine
    return bundled_engine_path()
