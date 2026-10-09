# -*- coding: utf-8 -*-
"""Configuration for IraLens.

Single config file: ``~/.iralens/config.yaml`` (override with
``IRALENS_HOME``). Adapted from the capability-layer source's private
config: atomic writes, owner-only permissions, symlink rejection. Values may
be overridden per-process with ``IRALENS_<KEY>`` environment variables.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

_MAX_CONFIG_BYTES = 1024 * 1024


class ConfigError(RuntimeError):
    """Configuration error safe to show to the user."""


class ConfigSecurityError(ConfigError):
    """A config path could redirect credential reads or writes."""


def home_dir() -> Path:
    override = os.environ.get("IRALENS_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".iralens"


def config_path() -> Path:
    return home_dir() / "config.yaml"


def state_dir() -> Path:
    return home_dir() / "state"


def _reject_symlink(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise ConfigSecurityError(f"{label} must not be a symlink: {path}")
        parent = path.parent
        if parent.exists() and parent.is_symlink():
            raise ConfigSecurityError(f"{label} directory must not be a symlink: {parent}")
    except OSError as exc:
        raise ConfigSecurityError(f"cannot safely inspect {label}: {exc}") from exc


def _atomic_write_yaml(target: Path, data: dict) -> None:
    _reject_symlink(target, "config file")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, default_flow_style=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    if os.name != "nt":
        try:
            os.chmod(target.parent, stat.S_IRWXU)
        except OSError:
            pass


class Config:
    """Key/value config with env overrides (IRALENS_<UPPER_KEY>)."""

    def __init__(self, path: Optional[Path] = None, read_only: bool = False) -> None:
        self.path = Path(path) if path else config_path()
        self.read_only = read_only
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        self._data = {}
        try:
            _reject_symlink(self.path, "config file")
            if self.path.is_file() and self.path.stat().st_size <= _MAX_CONFIG_BYTES:
                raw = self.path.read_text(encoding="utf-8")
                loaded = yaml.safe_load(raw)
                if isinstance(loaded, dict):
                    self._data = loaded
        except FileNotFoundError:
            pass
        except ConfigSecurityError:
            raise
        except Exception:
            # Corrupt config is ignored rather than crashing every command.
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        env_val = os.environ.get(f"IRALENS_{key.upper()}")
        if env_val is not None and env_val != "":
            return env_val
        value = self._data.get(key, default)
        return value

    def set(self, key: str, value: Any) -> None:
        if self.read_only:
            raise ConfigError("config is read-only in this context")
        self._data[key] = value
        _atomic_write_yaml(self.path, self._data)

    def delete(self, key: str) -> None:
        if self.read_only:
            raise ConfigError("config is read-only in this context")
        self._data.pop(key, None)
        _atomic_write_yaml(self.path, self._data)

    def to_dict(self) -> dict:
        """Config without secret-looking values (safe to display)."""
        secret_markers = ("token", "cookie", "key", "secret", "password", "ct0")
        return {
            k: ("***" if any(m in k.lower() for m in secret_markers) else v)
            for k, v in self._data.items()
        }
