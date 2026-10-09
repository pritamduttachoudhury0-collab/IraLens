# -*- coding: utf-8 -*-
"""Read-only inspection of MCP bridge (mcporter) configuration.

Adapted from the capability-layer source: never starts remote servers during
health checks, never expands editor-import credential boundaries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Set

_MAX_CONFIG_BYTES = 1024 * 1024


def _config_paths():
    override = os.environ.get("MCPORTER_CONFIG")
    if override:
        yield Path(override).expanduser()
        return
    yield Path.home() / ".mcporter" / "mcporter.json"
    yield Path.home() / ".mcporter" / "mcporter.jsonc"
    yield Path.cwd() / "config" / "mcporter.json"


def mcporter_server_names() -> Set[str]:
    """Server names from the effective local mcporter config (best-effort)."""
    names: Set[str] = set()
    for path in _config_paths():
        try:
            if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
                continue
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            servers = payload.get("mcpServers")
            if isinstance(servers, dict):
                names.update(k.casefold() for k in servers if isinstance(k, str))
        except Exception:
            continue
    return names
