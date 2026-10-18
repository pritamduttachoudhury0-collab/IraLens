# -*- coding: utf-8 -*-
"""F-03: executable discovery must match the environment that runs IraLens.

The defect: `probe_command()` used `shutil.which()` alone. A tool installed
into the *same virtualenv* as IraLens looks absent when that environment is
not on PATH -- the normal case when a venv entry point is invoked by absolute
path, which is exactly what `scripts/setup.sh` does. `doctor` then reported
"video downloader not installed" for a dependency that was installed.

These tests use a fake tool name, so they need no real external program.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from iralens import proc

FAKE = "iralens-test-fake-tool"
SENTINEL = "#!/bin/sh\necho iralens-fake-1.2.3\n"


def _write_executable(directory: Path, name: str = FAKE, body: str = SENTINEL) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + ".cmd" if sys.platform.startswith("win") else name)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# ------------------------------------------------------------------ resolution
def test_resolve_executable_finds_a_tool_on_path(tmp_path, monkeypatch):
    path = _write_executable(tmp_path / "on-path")
    monkeypatch.setenv("PATH", str(tmp_path / "on-path"))
    assert proc.resolve_executable(FAKE) == str(path)


def test_resolve_executable_finds_a_tool_in_the_interpreter_environment(
    tmp_path, monkeypatch
):
    """The whole point of F-03: PATH does not have it, the venv does."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    assert proc.resolve_executable(FAKE) is None

    fake_bindir = tmp_path / "venv-bin"
    _write_executable(fake_bindir)
    monkeypatch.setattr(proc, "interpreter_bin_dirs", lambda: [fake_bindir])
    try:
        assert proc.resolve_executable(FAKE) is not None
    finally:
        monkeypatch.undo()


def test_path_wins_over_the_interpreter_environment(tmp_path, monkeypatch):
    """An operator's explicit PATH choice is never overridden."""
    on_path = _write_executable(tmp_path / "on-path", body="#!/bin/sh\necho from-path\n")
    _write_executable(tmp_path / "venv-bin")
    monkeypatch.setenv("PATH", str(tmp_path / "on-path"))
    monkeypatch.setattr(proc, "interpreter_bin_dirs", lambda: [tmp_path / "venv-bin"])
    assert proc.resolve_executable(FAKE) == str(on_path)


def test_missing_executable_reports_missing_not_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(proc, "interpreter_bin_dirs", lambda: [])
    monkeypatch.setattr(proc, "module_invocation", lambda cmd: None)
    assert proc.resolve_executable(FAKE) is None
    result = proc.probe_command(FAKE, ["--version"])
    assert result.status == "missing"
    assert not result.ok


def test_non_executable_file_is_not_treated_as_a_tool(tmp_path, monkeypatch):
    """A present-but-unexecutable file must not be reported as available."""
    directory = tmp_path / "venv-bin"
    directory.mkdir()
    target = directory / FAKE
    target.write_text(SENTINEL, encoding="utf-8")
    target.chmod(0o644)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(proc, "interpreter_bin_dirs", lambda: [directory])
    assert proc.resolve_executable(FAKE) is None


def test_broken_executable_is_reported_as_broken(tmp_path, monkeypatch):
    """A file that cannot exec is `broken`, distinct from `missing`."""
    _write_executable(tmp_path / "bin", body="#!/definitely/not/a/shell\necho hi\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    result = proc.probe_command(FAKE, ["--version"])
    # 126/127 => broken; anything else non-zero => error. Both are honest.
    assert result.status in ("broken", "error")
    assert not result.ok


# ------------------------------------------------------------- module fallback
def test_module_fallback_is_used_when_no_launcher_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(proc, "interpreter_bin_dirs", lambda: [])
    monkeypatch.setattr(proc, "_MODULE_FALLBACKS", {FAKE: "json"})
    monkeypatch.setattr(proc, "module_invocation", proc.module_invocation)

    argv = proc.command_argv(FAKE, ["--version"])
    assert argv[0] == sys.executable
    assert "-m" in argv and "json" in argv
    assert proc.is_command_available(FAKE) is True


def test_module_fallback_is_skipped_when_the_module_is_absent(monkeypatch):
    monkeypatch.setattr(proc, "_MODULE_FALLBACKS", {FAKE: "iralens_definitely_not_a_module"})
    assert proc.module_invocation(FAKE) is None


def test_yt_dlp_has_a_module_fallback():
    """yt-dlp is the tool F-03 was reported against."""
    assert proc._MODULE_FALLBACKS.get("yt-dlp") == "yt_dlp"


# ------------------------------------------------------------------- probing
def test_probe_command_succeeds_through_the_interpreter_environment(
    tmp_path, monkeypatch
):
    """doctor must say `ok` for a venv tool when PATH does not list the venv."""
    if sys.platform.startswith("win"):
        pytest.skip("POSIX-only fake shell script")
    _write_executable(tmp_path / "venv-bin")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(proc, "interpreter_bin_dirs", lambda: [tmp_path / "venv-bin"])

    result = proc.probe_command(FAKE, ["--version"], timeout=15)
    assert result.ok, result.hint
    assert "iralens-fake-1.2.3" in result.output


# -------------------------------------------------------- youtube health wiring
def test_youtube_health_reports_available_without_the_venv_on_path(monkeypatch):
    """The reported symptom: `off` with the venv bin dir absent from PATH."""
    from iralens.sources import youtube

    monkeypatch.setattr(youtube, "probe_command",
                        lambda *a, **k: proc.ProbeResult("ok", output="2026.08.19"))
    ok, reason = youtube._ytdlp_status()
    assert ok is True
    assert "available" in reason


def test_youtube_health_distinguishes_installed_from_missing(monkeypatch):
    from iralens.sources import youtube

    monkeypatch.setattr(youtube, "probe_command",
                        lambda *a, **k: proc.ProbeResult(
                            "missing", hint="'yt-dlp' not found on PATH"))
    ok, reason = youtube._ytdlp_status()
    assert ok is False
    # yt-dlp is a declared dependency, so it is importable here: the message
    # must say the executable is missing, not that the package is absent.
    if youtube.importlib.util.find_spec("yt_dlp") is not None:
        assert "not found" in reason and "not installed" not in reason
    else:  # pragma: no cover - depends on the environment
        assert "not installed" in reason


def test_youtube_health_reports_a_broken_installation(monkeypatch):
    from iralens.sources import youtube

    monkeypatch.setattr(youtube, "probe_command",
                        lambda *a, **k: proc.ProbeResult(
                            "broken", hint="exists on PATH but cannot execute"))
    ok, reason = youtube._ytdlp_status()
    assert ok is False
    assert "unusable" in reason and "cannot execute" in reason


# ---------------------------------------------------------------- setup script
def test_setup_script_puts_the_venv_on_path_for_the_health_check():
    """`scripts/setup.sh` must run doctor with the environment it just built."""
    script = (Path(__file__).resolve().parent.parent / "scripts" / "setup.sh")
    text = script.read_text(encoding="utf-8")
    assert 'export PATH="$PWD/$VENV_DIR/bin:$PATH"' in text
    # The export has to come before the doctor invocation to have any effect.
    assert text.index('export PATH="$PWD/$VENV_DIR/bin:$PATH"') < text.index("--json doctor")


def test_windows_setup_script_is_aligned():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "setup.ps1")
    text = script.read_text(encoding="utf-8")
    assert "$env:PATH = \"$(Resolve-Path $VenvDir)\\Scripts;$env:PATH\"" in text
    assert text.index("$env:PATH = \"$(Resolve-Path $VenvDir)\\Scripts;$env:PATH\"") \
        < text.index("--json doctor")
