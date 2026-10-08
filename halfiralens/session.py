# -*- coding: utf-8 -*-
"""Unified session state for Half IraLens.

One session spans every capability: navigation history, discovered URLs with
provenance, and the browser engine's exported storage state (cookies + web
storage), persisted under ``~/.half-iralens/state/`` so context survives
across CLI invocations and engine restarts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import state_dir
from .model import utc_now_iso

_MAX_HISTORY = 200
_MAX_DISCOVERED = 500


class Session:
    """Persistent, capability-spanning session bookkeeping."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.dir = Path(directory) if directory else state_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "session.json"
        self.engine_state_file = self.dir / "browser_state.json"
        self.history: List[Dict[str, Any]] = []
        self.discovered: List[Dict[str, Any]] = []
        self.last_query: Optional[str] = None
        self.load()

    # ------------------------------------------------------------------ I/O
    def load(self) -> None:
        try:
            if self.file.is_file():
                data = json.loads(self.file.read_text(encoding="utf-8"))
                self.history = list(data.get("history", []))[-_MAX_HISTORY:]
                self.discovered = list(data.get("discovered", []))[-_MAX_DISCOVERED:]
                self.last_query = data.get("last_query")
        except Exception:
            self.history, self.discovered, self.last_query = [], [], None

    def save(self) -> None:
        payload = {
            "history": self.history[-_MAX_HISTORY:],
            "discovered": self.discovered[-_MAX_DISCOVERED:],
            "last_query": self.last_query,
            "updated_at": utc_now_iso(),
        }
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file)

    def reset(self) -> None:
        self.history, self.discovered, self.last_query = [], [], None
        self.save()
        if self.engine_state_file.exists():
            self.engine_state_file.unlink(missing_ok=True)

    # ------------------------------------------------------------- recording
    def record_open(
        self,
        url: str,
        *,
        title: str = "",
        source: str = "web",
        method: str = "",
    ) -> None:
        self.history.append(
            {
                "url": url,
                "title": title,
                "source": source,
                "method": method,
                "at": utc_now_iso(),
            }
        )
        self.history = self.history[-_MAX_HISTORY:]
        self.save()

    def record_discovery(
        self,
        url: str,
        *,
        title: str = "",
        source: str = "",
        found_via: str = "",
    ) -> None:
        for item in self.discovered:
            if item["url"] == url:
                item["title"] = title or item.get("title", "")
                item["seen_again_at"] = utc_now_iso()
                self.save()
                return
        self.discovered.append(
            {
                "url": url,
                "title": title,
                "source": source,
                "found_via": found_via,
                "at": utc_now_iso(),
            }
        )
        self.discovered = self.discovered[-_MAX_DISCOVERED:]
        self.save()

    # --------------------------------------------------------- engine state
    def save_engine_state(self, state: Dict[str, Any]) -> None:
        tmp = self.engine_state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.engine_state_file)

    def load_engine_state(self) -> Optional[Dict[str, Any]]:
        try:
            if self.engine_state_file.is_file():
                return json.loads(self.engine_state_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        return None

    # ---------------------------------------------------------------- views
    def snapshot(self) -> Dict[str, Any]:
        return {
            "history": list(reversed(self.history))[:50],
            "history_total": len(self.history),
            "discovered": list(reversed(self.discovered))[:50],
            "discovered_total": len(self.discovered),
            "last_query": self.last_query,
            "browser_state_persisted": self.engine_state_file.exists(),
        }
