# -*- coding: utf-8 -*-
"""Install the Half IraLens browser engine (prebuilt binary download).

The engine is distributed as prebuilt release binaries. This helper fetches
the right one for the current platform into the per-user engine cache and
verifies it executes. No background services, no package managers required.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from ..errors import EngineUnavailableError
from ..proc import probe_command
from .locate import engine_cache_dir

ENGINE_RELEASE_BASE = "https://github.com/h4ckf0r0day/obscura/releases/download"
ENGINE_VERSION = "0.2.4"
#: Pinned release checksums are absent upstream; integrity relies on HTTPS +
#: the post-download execution probe below.

_PLATFORM_ASSETS = {
    ("x86_64", "Linux"): "obscura-x86_64-linux.tar.gz",
    ("aarch64", "Linux"): "obscura-aarch64-linux.tar.gz",
    ("arm64", "Linux"): "obscura-aarch64-linux.tar.gz",
    ("x86_64", "Darwin"): "obscura-x86_64-macos.tar.gz",
    ("arm64", "Darwin"): "obscura-aarch64-macos.tar.gz",
    ("AMD64", "Windows"): "obscura-x86_64-windows.zip",
}


def check_archive_member(name: str) -> None:
    """Refuse archive members that could write outside the extraction directory.

    zipfile already strips absolute paths and '..', but relying on that is
    implicit. Reject such names explicitly so the rule is visible and tested.
    """
    parts = Path(name.replace("\\", "/")).parts
    if not name or name.startswith(("/", "\\")) or Path(name).is_absolute() \
            or ".." in parts or (parts and ":" in parts[0]):
        raise EngineUnavailableError(
            "engine archive has an unsafe path; refusing to extract",
            detail=name[:200],
        )


def _asset_name() -> str:
    machine = platform.machine()
    system = platform.system()
    asset = _PLATFORM_ASSETS.get((machine, system))
    if not asset:
        raise EngineUnavailableError(
            f"no prebuilt browser engine for {system}/{machine}",
            hint="build the engine from source and set HIL_ENGINE_PATH to the binary",
        )
    return asset


def install_engine(target_dir: Optional[Path] = None, version: str = ENGINE_VERSION) -> Path:
    """Download + extract the engine binary; returns its path."""
    dest = Path(target_dir) if target_dir else engine_cache_dir()
    dest.mkdir(parents=True, exist_ok=True)

    asset = _asset_name()
    url = f"{ENGINE_RELEASE_BASE}/v{version}/{asset}"
    binary_name = "obscura.exe" if asset.endswith(".zip") else "obscura"
    final_path = dest / binary_name

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset
        try:
            with urllib.request.urlopen(url, timeout=300) as resp, open(archive, "wb") as fh:
                shutil.copyfileobj(resp, fh, length=1024 * 512)
        except Exception as exc:
            raise EngineUnavailableError(
                "could not download the browser engine",
                hint=f"download manually from {url} and set HIL_ENGINE_PATH",
                detail=str(exc),
            ) from exc

        extracted: list[Path] = []
        if asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for name in zf.namelist():
                    check_archive_member(name)
                # nosec B202: every member name was validated by check_archive_member
                # above. zipfile creates no symlinks, so this cannot escape `tmp`.
                zf.extractall(tmp)  # nosec B202
            extracted = [Path(tmp) / n for n in zf.namelist()]
        else:
            with tarfile.open(archive, "r:gz") as tf:
                members = [m for m in tf.getmembers() if m.isfile()]
                tf.extractall(tmp, members=members, filter="data")
            extracted = [Path(tmp) / m.name for m in members]

        for candidate in extracted:
            if candidate.name == binary_name and candidate.is_file():
                shutil.copy2(candidate, final_path)
                break
        else:
            raise EngineUnavailableError("engine archive did not contain the expected binary")

    os.chmod(final_path, os.stat(final_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    probe = probe_command(str(final_path), ["--version"], timeout=15)
    if not probe.ok:
        final_path.unlink(missing_ok=True)
        raise EngineUnavailableError(
            "downloaded engine binary failed its startup probe",
            hint="your platform may need a different build; set HIL_ENGINE_PATH manually",
            detail=probe.hint,
        )
    return final_path
