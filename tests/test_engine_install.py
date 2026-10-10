# -*- coding: utf-8 -*-
"""Safe browser-engine installation (D-070): retries, checksum policy,
verify-before-replace atomicity, and honest status reporting. Offline."""

import hashlib
import io
import os
import tarfile
import urllib.error
from pathlib import Path

import pytest

from iralens.engine import install as install_mod
from iralens.engine.install import (
    _download,
    _sha256_of,
    engine_status,
    install_engine,
)
from iralens.errors import EngineUnavailableError
from iralens.proc import ProbeResult


def _make_archive(tmp_path: Path, binary_body: bytes = b"#!/bin/sh\necho 0.2.4\n") -> bytes:
    """A real tar.gz containing an 'obscura' member."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = binary_body
        info = tarfile.TarInfo("obscura")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_download(monkeypatch, archive: bytes, fail_times: int = 0, http_code: int = 0):
    state = {"calls": 0}

    def fake_urlopen(url, timeout=None):
        state["calls"] += 1
        if state["calls"] <= fail_times:
            if http_code:
                raise urllib.error.HTTPError(url, http_code, "err", {}, None)
            raise urllib.error.URLError("connection reset")
        return FakeResponse(archive)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return state


def _ok_probe(path, args=None, timeout=None):
    return ProbeResult("ok", output="0.2.4")


def _broken_probe(path, args=None, timeout=None):
    return ProbeResult("broken", hint="cannot execute")


# ------------------------------------------------------------------ retries
def test_download_retries_transient_failures(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    state = _patch_download(monkeypatch, archive, fail_times=2)
    dest = tmp_path / "a.tar.gz"
    sleeps = []
    _download("https://example.test/asset", dest, max_attempts=3, sleep=sleeps.append)
    assert state["calls"] == 3
    assert dest.read_bytes() == archive
    assert len(sleeps) == 2 and sleeps[0] <= sleeps[1]  # backoff


def test_download_gives_up_after_max_attempts(tmp_path, monkeypatch):
    _patch_download(monkeypatch, b"", fail_times=99)
    with pytest.raises(EngineUnavailableError):
        _download("https://example.test/asset", tmp_path / "x", max_attempts=3,
                  sleep=lambda s: None)


def test_download_does_not_retry_4xx(tmp_path, monkeypatch):
    state = _patch_download(monkeypatch, b"", fail_times=99, http_code=404)
    with pytest.raises(EngineUnavailableError) as exc_info:
        _download("https://example.test/asset", tmp_path / "x", max_attempts=5,
                  sleep=lambda s: None)
    assert state["calls"] == 1
    assert "404" in exc_info.value.message


# ----------------------------------------------------------------- checksum
def test_checksum_mismatch_aborts_before_install(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    _patch_download(monkeypatch, archive)
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    dest = tmp_path / "cache"
    with pytest.raises(EngineUnavailableError) as exc_info:
        install_engine(dest, expected_sha256="0" * 64, sleep=lambda s: None)
    assert "SHA-256" in exc_info.value.message
    assert not (dest / "obscura").exists()      # nothing was installed


def test_matching_checksum_installs_and_reports(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    _patch_download(monkeypatch, archive)
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    report = {}
    path = install_engine(tmp_path / "cache",
                          expected_sha256=hashlib.sha256(archive).hexdigest(),
                          report=report, sleep=lambda s: None)
    assert path.is_file()
    assert report["checksum_verified"] is True


def test_no_published_checksum_is_reported_honestly(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    _patch_download(monkeypatch, archive)
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    report = {}
    install_engine(tmp_path / "cache", report=report, sleep=lambda s: None)
    assert report["checksum_verified"] is False        # not claimed
    assert report["checksum_source"] == "none published"


def test_sha256_of(tmp_path):
    f = tmp_path / "blob"
    f.write_bytes(b"hello engine")
    assert _sha256_of(f) == hashlib.sha256(b"hello engine").hexdigest()


# --------------------------------------------------------- atomicity/rollback
def test_failed_probe_leaves_existing_engine_untouched(tmp_path, monkeypatch):
    dest = tmp_path / "cache"
    dest.mkdir()
    old = dest / "obscura"
    old.write_bytes(b"PREVIOUS WORKING ENGINE")

    archive = _make_archive(tmp_path, binary_body=b"NEW BUT BROKEN")
    _patch_download(monkeypatch, archive)
    monkeypatch.setattr(install_mod, "probe_command", _broken_probe)
    with pytest.raises(EngineUnavailableError) as exc_info:
        install_engine(dest, force=True, sleep=lambda s: None)
    assert old.read_bytes() == b"PREVIOUS WORKING ENGINE"   # rollback: untouched
    assert "untouched" in exc_info.value.message


def test_successful_install_replaces_atomically(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    _patch_download(monkeypatch, archive)
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    path = install_engine(tmp_path / "cache", sleep=lambda s: None)
    assert path.is_file()
    import os
    assert os.access(path, os.X_OK)
    assert path.read_bytes() == b"#!/bin/sh\necho 0.2.4\n"


def test_working_engine_is_not_redownloaded(tmp_path, monkeypatch):
    dest = tmp_path / "cache"
    dest.mkdir()
    (dest / "obscura").write_bytes(b"already here")
    state = _patch_download(monkeypatch, _make_archive(tmp_path))
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    report = {}
    path = install_engine(dest, report=report, sleep=lambda s: None)
    assert state["calls"] == 0                    # no download happened
    assert report.get("already_installed") is True
    assert path.read_bytes() == b"already here"


def test_archive_without_binary_is_rejected(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"readme only"
        info = tarfile.TarInfo("README.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    _patch_download(monkeypatch, buf.getvalue())
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    with pytest.raises(EngineUnavailableError):
        install_engine(tmp_path / "cache", sleep=lambda s: None)


# ------------------------------------------------------------------- status
def test_engine_status_reports_not_installed(monkeypatch):
    monkeypatch.setattr(install_mod, "find_engine", lambda config=None: None)
    status = engine_status()
    assert status["installed"] is False
    assert "install-engine" in str(status["message"])


def test_engine_status_reports_executable_state(monkeypatch, tmp_path):
    fake_path = tmp_path / "obscura"
    fake_path.write_bytes(b"x")
    monkeypatch.setattr(install_mod, "find_engine", lambda config=None: fake_path)
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    status = engine_status()
    assert status["installed"] is True
    assert status["executable"] is True
    assert status["path"] == str(fake_path)


# ------------------------------------------------- manual URL + PATH shim
def test_engine_download_url_names_platform_asset():
    from iralens.engine.install import ENGINE_RELEASE_BASE, ENGINE_VERSION, engine_download_url

    url = engine_download_url()
    assert url.startswith(f"{ENGINE_RELEASE_BASE}/v{ENGINE_VERSION}/")
    assert url.endswith(install_mod._asset_name())


def test_install_path_shim_writes_executable_wrapper(tmp_path):
    from iralens.engine.install import install_path_shim

    binary = tmp_path / "obscura"
    binary.write_text("#!/bin/sh\necho 0.2.4\n")
    bin_dir = tmp_path / "bin"
    shim = install_path_shim(binary, bin_dir)
    assert shim is not None and shim.is_file()
    assert shim.name == "iralens-engine"
    assert os.access(shim, os.X_OK)
    body = shim.read_text()
    assert body.startswith("#!/bin/sh")
    assert str(binary) in body


def test_install_engine_reports_shim(tmp_path, monkeypatch):
    monkeypatch.setattr(install_mod, "probe_command", _ok_probe)
    _patch_download(monkeypatch, _make_archive(tmp_path))
    report = {}
    path = install_engine(target_dir=tmp_path / "engine", report=report)
    assert path.is_file()
    assert report["shim"] is not None
    assert Path(report["shim"]).name == "iralens-engine"


def test_engine_status_reports_shim_presence(monkeypatch, tmp_path):
    from iralens.engine.install import install_path_shim

    monkeypatch.setattr(install_mod, "find_engine", lambda: None)
    st = engine_status()
    assert st["installed"] is False
    assert st.get("shim") is None
    shim = install_path_shim(tmp_path / "obscura")
    st = engine_status()
    assert st["shim"] == str(shim)
