# -*- coding: utf-8 -*-
"""IraLens internally maintained browser engine subsystem.

Contains the vendored Obscura browser engine (`vendor/obscura` at commit
`bd9529418b7fa975b50eabeac415cfaaa865a588`, Apache-2.0) and its native V8 runtime,
SSRF guard, RFC 6265 cookie jar, and MCP JSON-RPC 2.0 server.
"""

from __future__ import annotations

from .builder import (
    BUNDLED_BIN_DIR,
    TRACKED_LAUNCHER_PATH,
    build_rust_vendor_binary,
    ensure_bundled_launcher,
    ensure_generated_launcher,
    install_bundled_engine,
    relocatable_launcher_content,
)
from .cookies import CookieJar, StoredCookie
from .mcp_server import ENGINE_SERVER_NAME, ENGINE_SERVER_VERSION, MCP_TOOLS_SCHEMA
from .runtime import (
    PACKAGE_DIR,
    UPSTREAM_BASE_TAG,
    UPSTREAM_BASE_TAG_COMMIT,
    UPSTREAM_REPOSITORY,
    UPSTREAM_REVISION,
    VENDOR_OBSCURA_DIR,
    NavigationOutcome,
    ObscuraBrowserContext,
    ObscuraNativeEngine,
    V8PageIsolate,
)
from .ssrf import allow_private_network, is_blocked_ip, validate_navigation_url

__all__ = [
    "BUNDLED_BIN_DIR",
    "TRACKED_LAUNCHER_PATH",
    "CookieJar",
    "ENGINE_SERVER_NAME",
    "ENGINE_SERVER_VERSION",
    "MCP_TOOLS_SCHEMA",
    "NavigationOutcome",
    "ObscuraBrowserContext",
    "ObscuraNativeEngine",
    "PACKAGE_DIR",
    "StoredCookie",
    "UPSTREAM_BASE_TAG",
    "UPSTREAM_BASE_TAG_COMMIT",
    "UPSTREAM_REPOSITORY",
    "UPSTREAM_REVISION",
    "V8PageIsolate",
    "VENDOR_OBSCURA_DIR",
    "allow_private_network",
    "build_rust_vendor_binary",
    "ensure_bundled_launcher",
    "ensure_generated_launcher",
    "install_bundled_engine",
    "relocatable_launcher_content",
    "is_blocked_ip",
    "validate_navigation_url",
]
