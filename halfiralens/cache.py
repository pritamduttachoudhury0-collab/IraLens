# -*- coding: utf-8 -*-
"""Disk cache with TTL for search responses and static page reads.

Policy (documented in docs/CACHING.md):
  - Keys hash a namespace plus the full request: query, filters, options and
    the engine chain. Different filters or engines never share an entry.
  - Entries expire after a TTL taken from `Settings`. `get()` reports age and
    freshness so callers can tell the user what they got.
  - `cacheable=False` refuses the write. Callers must pass False for anything
    authenticated or session-bound. Search and static reads are public, so
    they are cacheable. Browser page state never reaches this cache.
  - Entries are plain JSON in the per-user state directory (never the repo).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .config import state_dir


class ResponseCache:
    def __init__(
        self,
        directory: Optional[Path] = None,
        *,
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.dir = Path(directory) if directory else state_dir() / "cache"
        self.enabled = enabled
        self._clock = clock

    @staticmethod
    def key(namespace: str, payload: Dict[str, Any]) -> str:
        blob = json.dumps({"ns": namespace, "p": payload}, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, namespace: str, payload: Dict[str, Any], ttl_seconds: float) -> Optional[Tuple[Any, float]]:
        """Return (value, age_seconds) when a fresh entry exists, else None."""
        if not self.enabled or ttl_seconds <= 0:
            return None
        path = self._path(self.key(namespace, payload))
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        age = self._clock() - float(entry.get("stored_at", 0))
        if age < 0 or age > ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        return entry.get("value"), age

    def put(self, namespace: str, payload: Dict[str, Any], value: Any, *, cacheable: bool = True) -> bool:
        if not self.enabled or not cacheable:
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(self.key(namespace, payload))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"namespace": namespace, "stored_at": self._clock(), "value": value},
                       ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return True

    def invalidate(self, namespace: Optional[str] = None) -> int:
        """Remove entries (all, or only one namespace). Returns the count removed."""
        if not self.dir.is_dir():
            return 0
        removed = 0
        for path in self.dir.glob("*.json"):
            if namespace is not None:
                try:
                    if json.loads(path.read_text(encoding="utf-8")).get("namespace") != namespace:
                        continue
                except (json.JSONDecodeError, OSError):
                    continue
            path.unlink(missing_ok=True)
            removed += 1
        return removed
