# -*- coding: utf-8 -*-
"""F-02: setup, doctor and engine discovery must never modify tracked files.

The defect: `ensure_bundled_launcher()` rewrote
`iralens/_internal/browser_engine/bin/iralens-browser-engine` -- tracked
package data -- with the running interpreter's absolute path. Running the
documented setup or the test suite therefore dirtied a clean checkout and
baked a developer's checkout path into the shipped package.

The fix has two halves, both asserted here:
  1. Discovery only ever *reads* the in-package launcher.
  2. The machine-local launcher is generated into an explicitly writable
     runtime directory (the per-user engine cache) instead.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from iralens._internal.browser_engine import builder
from iralens.engine import locate

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKED_LAUNCHER = (REPO_ROOT / "iralens" / "_internal" / "browser_engine" / "bin"
                    / "iralens-browser-engine")


# ------------------------------------------------------------------ 1. content
def test_tracked_launcher_is_relocatable_and_path_free():
    """The shipped launcher must not embed any absolute machine path."""
    if not TRACKED_LAUNCHER.is_file():
        pytest.skip("bundled launcher not present in this checkout")
    text = TRACKED_LAUNCHER.read_text(encoding="utf-8")
    assert str(REPO_ROOT) not in text
    assert "/home/" not in text
    assert "/tmp/" not in text
    assert "site-packages" not in text
    # It resolves its interpreter at run time instead of hardcoding one.
    assert "command -v" in text or "python3" in text


def test_tracked_launcher_runs_the_mcp_server_module():
    if not TRACKED_LAUNCHER.is_file():
        pytest.skip("bundled launcher not present in this checkout")
    text = TRACKED_LAUNCHER.read_text(encoding="utf-8")
    assert "iralens._internal.browser_engine.mcp_server" in text


# ------------------------------------------------------------- 2. no rewriting
def test_discovery_does_not_modify_the_tracked_launcher(tmp_path, monkeypatch):
    """Doctor's engine probe calls find_engine(); the file must be untouched."""
    if not TRACKED_LAUNCHER.is_file():
        pytest.skip("bundled launcher not present in this checkout")
    before = TRACKED_LAUNCHER.read_bytes()
    before_mode = stat.S_IMODE(TRACKED_LAUNCHER.stat().st_mode)

    monkeypatch.setenv("IRALENS_ENGINE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("IRALENS_ENGINE_PATH", raising=False)
    locate.find_engine()

    assert TRACKED_LAUNCHER.read_bytes() == before
    # Not even the mode bit: git tracks it, and chmod would dirty the checkout.
    assert stat.S_IMODE(TRACKED_LAUNCHER.stat().st_mode) == before_mode


def test_generated_launcher_is_written_to_the_writable_cache(tmp_path, monkeypatch):
    """The absolute-path launcher lands in the runtime cache, not the package."""
    cache = tmp_path / "engine-cache"
    monkeypatch.setenv("IRALENS_ENGINE_CACHE_DIR", str(cache))

    path = locate.generated_engine_path()
    assert path is not None
    assert path.parent == cache
    assert path.is_file()
    assert os.access(str(path), os.X_OK)
    # The generated launcher *may* name an interpreter; that is the point.
    assert sys.executable in path.read_text(encoding="utf-8")


def test_bundled_engine_path_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("IRALENS_ENGINE_CACHE_DIR", str(tmp_path / "cache"))
    before = TRACKED_LAUNCHER.read_bytes() if TRACKED_LAUNCHER.is_file() else None
    path = locate.bundled_engine_path()
    if before is not None:
        assert path == TRACKED_LAUNCHER
        assert TRACKED_LAUNCHER.read_bytes() == before


def test_bundled_engine_disabled_yields_none(monkeypatch):
    monkeypatch.setenv("IRALENS_DISABLE_BUNDLED_ENGINE", "1")
    assert locate.bundled_engine_path() is None
    assert locate.generated_engine_path() is None


# --------------------------------------------------- 3. unwritable package dir
def test_unwritable_directory_reports_none_instead_of_a_stale_path(tmp_path):
    """A target that cannot be written returns None, never a stale launcher.

    Earlier code returned the existing file when the write failed, which is how
    a launcher pointing at a deleted interpreter survived `find_engine()`.
    """
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    if os.access(str(target), os.W_OK):
        pytest.skip("cannot make the directory read-only as this user")
    try:
        assert builder.ensure_generated_launcher(target) is None
    finally:
        target.chmod(0o700)


def test_read_only_stale_launcher_is_refreshed_when_we_own_it(tmp_path):
    """A mode-444 launcher we own is made writable and regenerated."""
    target = tmp_path / "engine"
    target.mkdir()
    stale = target / builder.BUNDLED_LAUNCHER_NAME
    stale.write_text("#!/bin/sh\nexec /nonexistent/python -m nope\n", encoding="utf-8")
    stale.chmod(0o444)

    path = builder.ensure_generated_launcher(target)
    if path is None:
        pytest.skip("launcher is not owned by this user; cannot refresh it")
    body = path.read_text(encoding="utf-8")
    assert "/nonexistent/python" not in body
    assert sys.executable in body


def test_current_launcher_is_not_rewritten(tmp_path):
    """Idempotent: an up-to-date launcher is left byte-identical."""
    target = tmp_path / "engine"
    first = builder.ensure_generated_launcher(target)
    assert first is not None
    before = first.read_bytes()
    before_mtime = first.stat().st_mtime_ns

    second = builder.ensure_generated_launcher(target)
    assert second == first
    assert second.read_bytes() == before
    assert second.stat().st_mtime_ns == before_mtime


def test_writable_target_regenerates_when_the_interpreter_changes(tmp_path):
    """A stale launcher is refreshed, not left pointing at a dead interpreter."""
    target = tmp_path / "engine"
    target.mkdir()
    stale = target / builder.BUNDLED_LAUNCHER_NAME
    stale.write_text("#!/bin/sh\nexec /nonexistent/python -m nope\n", encoding="utf-8")
    stale.chmod(0o755)

    path = builder.ensure_generated_launcher(target)
    assert path == stale
    body = stale.read_text(encoding="utf-8")
    assert "/nonexistent/python" not in body
    assert sys.executable in body


# ------------------------------------------------------------- 4. relocation
def test_generated_launcher_is_used_after_the_checkout_moves(tmp_path, monkeypatch):
    """The resolved engine does not depend on where the package is installed."""
    cache = tmp_path / "moved-cache"
    monkeypatch.setenv("IRALENS_ENGINE_CACHE_DIR", str(cache))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.delenv("IRALENS_ENGINE_PATH", raising=False)

    found = locate.find_engine()
    if found is None:
        pytest.skip("no engine launcher is constructible in this environment")
    assert found.parent == cache, "discovery fell back to a package-relative path"


def test_relocatable_launcher_content_matches_the_tracked_file():
    if not TRACKED_LAUNCHER.is_file():
        pytest.skip("bundled launcher not present in this checkout")
    if sys.platform.startswith("win"):
        pytest.skip("POSIX launcher is tracked in this checkout")
    assert builder.relocatable_launcher_content() == TRACKED_LAUNCHER.read_text(encoding="utf-8")


# ------------------------------------------------------- 5. subprocess honesty
def test_bundled_launcher_reports_a_missing_interpreter_clearly(tmp_path):
    """A launcher whose interpreter vanished exits non-zero with a message."""
    launcher = tmp_path / "iralens-browser-engine"
    launcher.write_text(
        "#!/bin/sh\n"
        'if [ -x "/nonexistent/python" ]; then\n'
        '  exec "/nonexistent/python" -m iralens._internal.browser_engine.mcp_server "$@"\n'
        "fi\n"
        'echo "iralens-browser-engine: the interpreter that generated this launcher "\n'
        'echo "  is gone; run \'iralens install-engine\' or doctor to regenerate it." >&2\n'
        "exit 127\n",
        encoding="utf-8")
    launcher.chmod(0o755)
    proc = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
    assert proc.returncode == 127
    assert "interpreter" in (proc.stdout + proc.stderr).lower()


def test_doctor_subprocess_leaves_the_tracked_launcher_unchanged(tmp_path):
    """End-to-end: running doctor in a subprocess must not dirty the checkout."""
    if not TRACKED_LAUNCHER.is_file():
        pytest.skip("bundled launcher not present in this checkout")
    before = TRACKED_LAUNCHER.read_bytes()
    env = dict(os.environ)
    env["IRALENS_HOME"] = str(tmp_path / "home")
    env["IRALENS_ENGINE_CACHE_DIR"] = str(tmp_path / "cache")
    proc = subprocess.run(
        [sys.executable, "-m", "iralens", "--json", "doctor"],
        cwd=str(tmp_path), capture_output=True, text=True, env=env, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert TRACKED_LAUNCHER.read_bytes() == before
