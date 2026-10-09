# -*- coding: utf-8 -*-
"""Shared helper for sources served through a desktop browser-session bridge.

The bridge CLI (OpenCLI) reuses the user's already-logged-in Chrome session;
IraLens never logs in on the user's behalf and never reads browser
cookies itself. Health checks only use the side-effect-free ``--version``
probe plus the documented loopback daemon status endpoint.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from ..proc import probe_command

OPENCLI_PACKAGE = "@jackwener/opencli"
_DAEMON_STATUS_URL = "http://127.0.0.1:19825/status"
_UNSUPPORTED_APP_ENV = ("OPENCLI_DAEMON_PORT",)


@dataclass
class OpenCLIStatus:
    installed: bool
    broken: bool = False
    ready: bool = False          # daemon reports the browser extension connected
    hint: str = ""


def opencli_status() -> OpenCLIStatus:
    probe = probe_command(
        "opencli", ["--version"], timeout=10,
        package=OPENCLI_PACKAGE, remove_env=_UNSUPPORTED_APP_ENV,
    )
    if probe.status == "missing":
        return OpenCLIStatus(installed=False, hint=f"'{OPENCLI_PACKAGE}' not installed")
    if not probe.ok:
        return OpenCLIStatus(installed=True, broken=True, hint=probe.hint)
    try:
        req = urllib.request.Request(_DAEMON_STATUS_URL, headers={"X-OpenCLI": "1"}, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            status = json.loads(resp.read(64 * 1024).decode("utf-8", errors="replace"))
        connected = bool(status.get("connected") or status.get("extension_connected"))
        return OpenCLIStatus(
            installed=True, ready=connected,
            hint="" if connected else "bridge daemon up, but the browser extension is not connected",
        )
    except Exception:
        return OpenCLIStatus(
            installed=True, ready=False,
            hint="bridge daemon not reachable — is the desktop bridge running?",
        )
