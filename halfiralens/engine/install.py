# -*- coding: utf-8 -*-
"""Install the Half IraLens browser engine (prebuilt binary download).

The engine is distributed as prebuilt release binaries. This helper fetches
the right one for the current platform into the per-user engine cache and
verifies it executes. No background services, no package managers required.

Safety properties (D-070):
  - Atomic replacement: the archive is downloaded, extracted, and the binary
    is startup-probed *inside a temporary directory*. Only after the probe
    passes is the binary moved into place. A failed install never overwrites
    or deletes an existing working engine, and no unrelated file is touched.
  - Retries: transient download failures (timeouts, 5xx) are retried with
    backoff; 4xx and policy failures are not.
  - Integrity: if a SHA-256 is supplied (`expected_sha256` argument or the
    `HIL_ENGINE_SHA256` environment variable) the archive is verified against
    it before extraction, and a mismatch aborts the install. The upstream
    release publishes no checksum file, so without a supplied value the
    install relies on HTTPS plus the startup probe — and the status report
    says exactly that instead of claiming a verified checksum.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Optional

from ..errors import EngineUnavailableError
from ..proc import probe_command
from .locate import engine_cache_dir, find_engine

ENGINE_RELEASE_BASE = "https://github.com/h4ckf0r0day/obscura/releases/download"
ENGINE_VERSION = "0.2.4"
#: The upstream release publishes no checksum asset (checked via the GitHub
#: API for v0.2.4). Integrity therefore relies on HTTPS + the startup probe
#: unless the operator supplies a checksum of their own.
DOWNLOAD_TIMEOUT = 300
DOWNLOAD_ATTEMPTS = 3

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


def _download(url: str, dest: Path, *, max_attempts: int = DOWNLOAD_ATTEMPTS,
              sleep=time.sleep) -> None:
    """Download with retries for transient failures; 4xx is not retried."""
    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp, \
                    open(dest, "wb") as fh:
                shutil.copyfileobj(resp, fh, length=1024 * 512)
            return
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise EngineUnavailableError(
                    f"engine download rejected (HTTP {exc.code})",
                    hint=f"check that release v{ENGINE_VERSION} has this asset",
                    detail=url,
                ) from exc
            last_error = exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
        if attempt < max(1, max_attempts):
            sleep(min(8.0, 1.0 * (2 ** (attempt - 1))))
    raise EngineUnavailableError(
        "could not download the browser engine",
        hint=f"download manually from {url} and set HIL_ENGINE_PATH",
        detail=str(last_error),
    )


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract(archive: Path, asset: str, binary_name: str, stage: Path) -> Path:
    """Extract the archive into *stage* and return the staged binary path."""
    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                check_archive_member(name)
            # nosec B202: every member name was validated by check_archive_member
            # above. zipfile creates no symlinks, so this cannot escape `stage`.
            zf.extractall(stage)  # nosec B202
        candidates = list(stage.rglob(binary_name))
    else:
        with tarfile.open(archive, "r:gz") as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
            for member in members:
                check_archive_member(member.name)
            try:
                tf.extractall(stage, members=members, filter="data")
            except TypeError:
                # Python < 3.12 has no `filter` argument; member names were
                # validated above and only regular files are extracted.
                tf.extractall(stage, members=members)
        candidates = [stage / m.name for m in members if Path(m.name).name == binary_name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise EngineUnavailableError("engine archive did not contain the expected binary")


def engine_status() -> Dict[str, object]:
    """Report the current engine installation state without installing anything."""
    path = find_engine()
    report: Dict[str, object] = {"installed": path is not None}
    if path is None:
        report["message"] = "browser engine not installed — run: halfiralens install-engine"
        return report
    report["path"] = str(path)
    probe = probe_command(str(path), ["--version"], timeout=15)
    report["executable"] = probe.ok
    if probe.ok:
        report["version_output"] = (probe.output or "")[:120]
    else:
        report["message"] = f"engine present but not executable ({probe.status}); reinstall it"
    return report


def install_engine(
    target_dir: Optional[Path] = None,
    version: str = ENGINE_VERSION,
    *,
    force: bool = False,
    expected_sha256: Optional[str] = None,
    max_attempts: int = DOWNLOAD_ATTEMPTS,
    report: Optional[Dict[str, object]] = None,
    sleep=time.sleep,
) -> Path:
    """Download, verify, and atomically install the engine binary.

    Returns the installed binary path. When *report* is given, it is filled
    with status details (`already_installed`, `checksum_verified`, …) for
    honest status output. Raises EngineUnavailableError on any failure,
    leaving the previous installation untouched.
    """
    info = report if report is not None else {}
    dest = Path(target_dir) if target_dir else engine_cache_dir()
    dest.mkdir(parents=True, exist_ok=True)

    asset = _asset_name()
    binary_name = "obscura.exe" if asset.endswith(".zip") else "obscura"
    final_path = dest / binary_name

    # Skip re-downloading when a working engine is already in place.
    if not force and final_path.is_file():
        probe = probe_command(str(final_path), ["--version"], timeout=15)
        if probe.ok:
            info.update(already_installed=True, path=str(final_path))
            return final_path

    checksum = expected_sha256 or os.environ.get("HIL_ENGINE_SHA256") or ""
    url = f"{ENGINE_RELEASE_BASE}/v{version}/{asset}"

    with tempfile.TemporaryDirectory(prefix="hil-engine-") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / asset
        _download(url, archive, max_attempts=max_attempts, sleep=sleep)

        if checksum:
            actual = _sha256_of(archive)
            if actual != checksum.strip().lower():
                raise EngineUnavailableError(
                    "engine archive failed SHA-256 verification; not installed",
                    hint="the download does not match the expected checksum",
                    detail=f"expected {checksum.strip().lower()} got {actual}",
                )
            info["checksum_verified"] = True
        else:
            # Honest status: no published checksum was checked.
            info["checksum_verified"] = False

        stage = tmp_dir / "stage"
        stage.mkdir()
        staged = _extract(archive, asset, binary_name, stage)
        staged.chmod(staged.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        # Verify before touching the destination: a binary that cannot start
        # must never replace a working one.
        probe = probe_command(str(staged), ["--version"], timeout=15)
        if not probe.ok:
            raise EngineUnavailableError(
                "downloaded engine binary failed its startup probe; the previous "
                "installation (if any) was left untouched",
                hint="your platform may need a different build; set HIL_ENGINE_PATH manually",
                detail=probe.hint,
            )

        os.replace(staged, final_path)   # atomic swap into place
        info.update(already_installed=False, path=str(final_path),
                    checksum_source=("supplied" if checksum else "none published"))
    return final_path
