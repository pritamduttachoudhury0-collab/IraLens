# -*- coding: utf-8 -*-
"""Locate the Half IraLens browser engine binary.

Search order:
  1. ``HIL_ENGINE_PATH`` env var / ``engine_path`` config key
  2. ``halfiralens`` PATH shim installed by the engine installer
  3. the per-user engine cache (``~/.cache/half-iralens/engine``)
  4. ``tools/engine`` next to this project (vendored checkout)

If nothing is found, `EngineUnavailableError` explains how to install it
(``halfiralens install-engine``).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

#: Binary file name of the engine executable.
ENGINE_BINARY_NAMES = ("halfiralens-engine", "obscura")


def engine_cache_dir() -> Path:
    override = os.environ.get("HIL_ENGINE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "half-iralens" / "engine"


def _project_tools_dir() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "tools" / "engine"
        if candidate.is_dir():
            return candidate
        if (parent / "pyproject.toml").is_file():
            return None
    return None


def find_engine(config=None) -> Optional[Path]:
    """Return the engine binary path, or None when not installed."""
    candidates: list[Path] = []

    explicit = None
    env_path = os.environ.get("HIL_ENGINE_PATH")
    if env_path:
        explicit = Path(env_path).expanduser()
    elif config is not None:
        configured = config.get("engine_path")
        if configured:
            explicit = Path(str(configured)).expanduser()
    if explicit:
        return explicit if explicit.is_file() and os.access(explicit, os.X_OK) else None

    for name in ENGINE_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    for name in ENGINE_BINARY_NAMES:
        candidates.append(engine_cache_dir() / name)

    tools = _project_tools_dir()
    if tools:
        for name in ENGINE_BINARY_NAMES:
            candidates.append(tools / name)

    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None
