# -*- coding: utf-8 -*-
"""Unified session state for IraLens.

One session spans every capability: navigation history, discovered URLs with
provenance, and the browser engine's exported storage state (cookies + web
storage), persisted under ``~/.iralens/state/`` so context survives
across CLI invocations and engine restarts.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .config import state_dir
from .model import utc_now_iso

_MAX_HISTORY = 200
_MAX_DISCOVERED = 500

try:  # POSIX advisory locking; optional (Windows has no fcntl).
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

#: In-process serialization for the state files. A module-level registry
#: keyed by the resolved path, so two Session objects in one process cannot
#: interleave their read-modify-write cycles.
_FILE_LOCKS: Dict[str, "threading.RLock"] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> "threading.RLock":
    key = str(path)
    with _LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive lock for *path* across threads and processes.

    The process-level lock is a sidecar ``.lock`` file so it never has to
    be replaced (and never disappears under a concurrent writer).
    """
    lock = _lock_for(path)
    with lock:
        lock_path = path.with_name(path.name + ".lock")
        handle = None
        try:
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = open(lock_path, "a+")
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError:
                handle = None
            yield
        finally:
            if handle is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    handle.close()
                except OSError:
                    pass


def _atomic_write_json(target: Path, payload: Any) -> None:
    """Write JSON to *target* atomically, privately and race-free.

    The temporary file has a **unique** name (it used to be a fixed
    ``session.tmp``), so two concurrent writers cannot truncate or replace
    each other's staging file. The result is written with owner-only
    permissions: the file holds cookies and the full browsing history.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                     prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp already creates 0600; make it explicit and portable.
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - best effort
            pass
        os.replace(tmp, target)
        try:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - best effort
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _union_into(live: List[Dict[str, Any]], incoming: List[Dict[str, Any]],
                key) -> None:
    """Add the records of *incoming* that *live* does not already have.

    The list object is never replaced, so a concurrent `append` to the same
    list survives the update.
    """
    known = set()
    for item in live:
        try:
            known.add(key(item))
        except Exception:  # pragma: no cover - defensive
            continue
    for item in incoming:
        try:
            marker = key(item)
        except Exception:  # pragma: no cover - defensive
            continue
        if marker in known:
            continue
        known.add(marker)
        live.append(item)


def _merge_history(on_disk: Any, local: Any) -> List[Dict[str, Any]]:
    """Union two history lists, keeping order and dropping exact duplicates."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for source in (on_disk or [], local or []):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            key = (item.get("url"), item.get("at"))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _merge_discovered(on_disk: Any, local: Any) -> List[Dict[str, Any]]:
    """Union two discovery lists by URL; the newer title wins."""
    out: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}
    for source in (on_disk or [], local or []):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url:
                continue
            if url in index:
                position = index[url]
                existing = out[position]
                if str(item.get("title") or "") and not str(existing.get("title") or ""):
                    existing["title"] = item.get("title")
                new_at = str(item.get("at") or "")
                old_at = str(existing.get("at") or "")
                if new_at and new_at > old_at:
                    existing["at"] = item.get("at")
                continue
            index[url] = len(out)
            out.append(dict(item))
    return out


class Session:
    """Persistent, capability-spanning session bookkeeping."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.dir = Path(directory) if directory else state_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        # The state directory holds cookies and the full browsing history:
        # owner-only, always.
        try:
            os.chmod(self.dir, stat.S_IRWXU)
        except OSError:  # pragma: no cover - best effort
            pass
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

    def save(self, *, merge: bool = True) -> None:
        """Persist the session atomically, without losing concurrent writes.

        The write is a read-modify-write under an exclusive lock, and the
        on-disk state is *merged* with the in-memory state rather than
        overwritten. Two processes saving at the same time used to race on
        a fixed ``session.tmp`` name: one lost the file entirely
        (``FileNotFoundError``) and the other lost the other's records.

        `merge=False` replaces the file outright — that is what a reset
        must do, or the cleared state would be merged back from disk.
        """
        with _exclusive(self.file):
            self._write_locked(merge=merge)

    def _write_locked(self, *, merge: bool = True) -> None:
        """Write the session. The caller must hold the file's lock."""
        on_disk = self._read_disk() if merge else {}
        history = _merge_history(on_disk.get("history"), self.history)
        discovered = _merge_discovered(on_disk.get("discovered"), self.discovered)
        last_query = self.last_query or on_disk.get("last_query")
        written_history: List[Dict[str, Any]] = history[-_MAX_HISTORY:]
        written_discovered: List[Dict[str, Any]] = discovered[-_MAX_DISCOVERED:]
        payload: Dict[str, Any] = {
            "history": written_history,
            "discovered": written_discovered,
            "last_query": last_query,
            "updated_at": utc_now_iso(),
        }
        _atomic_write_json(self.file, payload)
        # Fold the written state back into the live lists *without
        # replacing the list objects*: another thread may be appending to
        # them right now, and swapping the object would drop its record.
        _union_into(self.history, written_history,
                    key=lambda i: (i.get("url"), i.get("at")))
        _union_into(self.discovered, written_discovered, key=lambda i: i.get("url"))
        self.last_query = last_query

    def _read_disk(self) -> Dict[str, Any]:
        try:
            if self.file.is_file():
                data = json.loads(self.file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def reset(self) -> None:
        with _exclusive(self.file):
            self.history.clear()
            self.discovered.clear()
            self.last_query = None
            # A reset replaces the stored state; merging would bring the very
            # records that were just cleared straight back from disk.
            self._write_locked(merge=False)
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
        with _exclusive(self.file):
            self.history.append(
                {
                    "url": url,
                    "title": title,
                    "source": source,
                    "method": method,
                    "at": utc_now_iso(),
                }
            )
            del self.history[:-_MAX_HISTORY]
            self._write_locked()

    def record_discovery(
        self,
        url: str,
        *,
        title: str = "",
        source: str = "",
        found_via: str = "",
    ) -> None:
        with _exclusive(self.file):
            for item in self.discovered:
                if item["url"] == url:
                    item["title"] = title or item.get("title", "")
                    item["seen_again_at"] = utc_now_iso()
                    self._write_locked()
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
            del self.discovered[:-_MAX_DISCOVERED]
            self._write_locked()

    # --------------------------------------------------------- engine state
    def save_engine_state(self, state: Dict[str, Any]) -> None:
        """Persist the browser's storage state atomically and privately."""
        with _exclusive(self.engine_state_file):
            _atomic_write_json(self.engine_state_file, state)

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
