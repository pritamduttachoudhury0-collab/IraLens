# -*- coding: utf-8 -*-
"""Native V8 + Obscura Browser Runtime for IraLens.

Executes the vendored Obscura JavaScript/DOM engine (`vendor/obscura/crates/obscura-js/js/bootstrap.js`,
`indexed_db.js`, and `src/markdown.rs`) inside an isolated V8 runtime (`py_mini_racer` /
`libmini_racer`, with automatic fallback to a persistent Node.js V8 VM worker if
`py_mini_racer` is unavailable), paired with Obscura's SSRF guard (`ssrf.py`),
RFC 6265 cookie jar (`cookies.py`), origin storage, and MCP tool dispatcher.

Upstream source revision: `bd9529418b7fa975b50eabeac415cfaaa865a588` (Apache-2.0).
"""

from __future__ import annotations

import base64
import datetime
import http.client
import json
import mimetypes
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ...errors import (
    EngineUnavailableError,
    ExtractionError,
    OperationTimeoutError,
    PageUnavailableError,
    SecurityBlockedError,
)
from ...extract import looks_like_pdf, pdf_to_text
from .cookies import CookieJar
from .ssrf import allow_private_network, validate_navigation_url
from .stealth import (
    ContentEncodingError,
    StealthProfile,
    build_stealth_capability,
    decode_body,
)

PACKAGE_DIR = Path(__file__).resolve().parent
VENDOR_OBSCURA_DIR = PACKAGE_DIR / "vendor" / "obscura"
UPSTREAM_REPOSITORY = "https://github.com/h4ckf0r0day/obscura"
UPSTREAM_REVISION = "bd9529418b7fa975b50eabeac415cfaaa865a588"
UPSTREAM_BASE_TAG = "v0.2.4"
UPSTREAM_BASE_TAG_COMMIT = "1fccab2c1ecd0f7eadc16d7a520ba227f9e7f782"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
FETCH_REDIRECT_LIMIT = 20
MAX_NAVIGATION_BODY_BYTES = 10 * 1024 * 1024
MAX_SUBRESOURCE_SCRIPTS = 16
MAX_SUBRESOURCE_SCRIPT_BYTES = 2 * 1024 * 1024
DEFAULT_V8_TIMEOUT_SEC = 15.0
DEFAULT_V8_MAX_MEMORY_BYTES = 256 * 1024 * 1024


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


_BUNDLE_LOCK = threading.Lock()
_CACHED_BUNDLE: Optional[Tuple[str, str, str]] = None


def _load_vendored_js_bundle() -> Tuple[str, str, str]:
    """Load `(host_ops_js, obscura_bootstrap_js, host_mcp_js)` from disk once."""
    global _CACHED_BUNDLE
    with _BUNDLE_LOCK:
        if _CACHED_BUNDLE is not None:
            return _CACHED_BUNDLE

        host_ops_path = PACKAGE_DIR / "host_ops.js"
        bootstrap_path = VENDOR_OBSCURA_DIR / "crates" / "obscura-js" / "js" / "bootstrap.js"
        idb_path = VENDOR_OBSCURA_DIR / "crates" / "obscura-js" / "js" / "indexed_db.js"
        markdown_rs_path = VENDOR_OBSCURA_DIR / "crates" / "obscura-js" / "src" / "markdown.rs"
        host_mcp_path = PACKAGE_DIR / "host_mcp.js"

        for p in (host_ops_path, bootstrap_path, idb_path, markdown_rs_path, host_mcp_path):
            if not p.is_file():
                raise EngineUnavailableError(
                    f"Bundled browser engine asset is missing: {p.name}",
                    detail="missing_engine_asset",
                )

        host_ops_js = host_ops_path.read_text(encoding="utf-8")
        bootstrap_raw = bootstrap_path.read_text(encoding="utf-8")
        idb_raw = idb_path.read_text(encoding="utf-8")
        bootstrap_js = bootstrap_raw.replace("/* INDEXED_DB */", idb_raw)

        md_rs = markdown_rs_path.read_text(encoding="utf-8")
        match = re.search(r'r#"(.*)"#;', md_rs, re.DOTALL)
        if not match:
            raise EngineUnavailableError(
                "Could not extract HTML_TO_MARKDOWN_JS from vendored markdown.rs",
                detail="corrupt_engine_asset",
            )
        md_js = match.group(1).strip()
        markdown_shim = (
            f"globalThis.__obscura_html_to_markdown = function() {{ return {md_js}; }};"
        )
        host_mcp_js = markdown_shim + "\n" + host_mcp_path.read_text(encoding="utf-8")

        _CACHED_BUNDLE = (host_ops_js, bootstrap_js, host_mcp_js)
        return _CACHED_BUNDLE


class _NodeVmFallbackIsolate:
    """Fallback V8 isolate using a persistent Node.js `vm` worker over pipes."""

    def __init__(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            raise EngineUnavailableError(
                "No V8 runtime available (neither py_mini_racer nor node is installed).",
                detail="v8_runtime_missing",
            )
        worker_script = (
            "const vm = require('vm');\n"
            "const readline = require('readline');\n"
            "const ctx = vm.createContext({});\n"
            "const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });\n"
            "rl.on('line', (line) => {\n"
            "  if (!line.trim()) return;\n"
            "  try {\n"
            "    const req = JSON.parse(line);\n"
            "    const res = vm.runInContext(req.code, ctx, { timeout: Math.max(50, Math.round((req.timeout || 15) * 1000)) });\n"
            "    process.stdout.write(JSON.stringify({ ok: true, value: res === undefined ? null : res }) + '\\n');\n"
            "  } catch (err) {\n"
            "    const msg = String(err && (err.message || err));\n"
            "    const isTimeout = msg.includes('Script execution timed out');\n"
            "    process.stdout.write(JSON.stringify({ ok: false, timeout: isTimeout, error: msg }) + '\\n');\n"
            "  }\n"
            "});\n"
        )
        self._proc = subprocess.Popen(
            [node_bin, "-e", worker_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def eval(self, code: str, timeout_sec: float = DEFAULT_V8_TIMEOUT_SEC) -> Any:
        if self._proc.poll() is not None or not self._proc.stdin or not self._proc.stdout:
            raise EngineUnavailableError("V8 worker process exited unexpectedly")
        payload = json.dumps({"code": code, "timeout": timeout_sec}) + "\n"
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise EngineUnavailableError("V8 worker closed stdout unexpectedly")
        resp = json.loads(line)
        if not resp.get("ok"):
            if resp.get("timeout"):
                raise OperationTimeoutError(
                    f"JavaScript execution timed out after {timeout_sec}s",
                    detail="script_timeout",
                )
            raise ExtractionError(
                "the page's JavaScript failed while the browser was rendering it",
                detail=str(resp.get("error", "unknown error")).splitlines()[0][:300],
            )
        return resp.get("value")

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


class V8PageIsolate:
    """Isolated V8 JavaScript runtime loaded with Obscura's `bootstrap.js`."""

    def __init__(
        self,
        *,
        max_memory_bytes: int = DEFAULT_V8_MAX_MEMORY_BYTES,
    ) -> None:
        self._lock = threading.RLock()
        self._max_memory = max_memory_bytes
        self._mr: Any = None
        self._node_fallback: Optional[_NodeVmFallbackIsolate] = None
        self._closed = False

        try:
            from py_mini_racer import MiniRacer  # type: ignore[import-untyped]

            self._mr = MiniRacer()
        except Exception:
            self._node_fallback = _NodeVmFallbackIsolate()

        host_ops_js, bootstrap_js, host_mcp_js = _load_vendored_js_bundle()
        self.eval(host_ops_js, timeout_sec=30.0)
        self.eval(bootstrap_js, timeout_sec=30.0)
        self.eval(host_mcp_js, timeout_sec=30.0)

    def eval(
        self,
        code: str,
        *,
        timeout_sec: float = DEFAULT_V8_TIMEOUT_SEC,
    ) -> Any:
        with self._lock:
            if self._closed:
                raise EngineUnavailableError("V8 isolate has been closed")
            if self._mr is not None:
                try:
                    return self._mr.eval(
                        code,
                        timeout_sec=max(0.05, float(timeout_sec)),
                        max_memory=self._max_memory,
                    )
                except Exception as exc:
                    cls_name = type(exc).__name__
                    msg = str(exc)
                    if "Timeout" in cls_name or "terminated by timeout" in msg.lower():
                        raise OperationTimeoutError(
                            f"Navigation timeout / JavaScript execution timed out after {timeout_sec}s",
                            detail="script_timeout",
                        ) from exc
                    if "Memory" in cls_name or "out of memory" in msg.lower():
                        raise ExtractionError(
                            "the browser ran out of memory while rendering the page",
                            detail="v8_heap_limit",
                        ) from exc
                    raise ExtractionError(
                        "the page's JavaScript failed while the browser was rendering it",
                        detail=str(msg).splitlines()[0][:300],
                    ) from exc
            assert self._node_fallback is not None
            return self._node_fallback.eval(code, timeout_sec=timeout_sec)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._node_fallback is not None:
                self._node_fallback.close()
                self._node_fallback = None
            self._mr = None


@dataclass
class NetworkEventRecord:
    url: str
    method: str
    status: int
    content_type: str
    resource_type: str
    redirected: bool
    timestamp: str


@dataclass
class NavigationOutcome:
    requested_url: str
    final_url: str
    title: str
    status_code: int
    content_type: str
    redirected: bool
    redirect_chain: List[str]
    retrieved_at: str
    encoding: str = "UTF-8"
    ok: bool = True
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "url": self.final_url,
            "title": self.title,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "redirected": self.redirected,
            "redirect_chain": list(self.redirect_chain),
            "retrieved_at": self.retrieved_at,
            "encoding": self.encoding,
            "ok": self.ok,
            "message": f"Navigated to {self.final_url}\nTitle: {self.title}",
        }
        if self.error:
            out["error"] = self.error
        if self.warnings:
            out["warnings"] = list(self.warnings)
        return out


class BrowserTab:
    """Single browser tab with its own V8 isolate, DOM, sessionStorage, and history."""

    def __init__(self, tab_id: str, context: "ObscuraBrowserContext") -> None:
        self.tab_id = tab_id
        self.context = context
        self.isolate = V8PageIsolate()
        self.requested_url: str = "about:blank"
        self.current_url: str = "about:blank"
        self.title: str = ""
        self.status_code: Optional[int] = None
        self.content_type: str = "text/html"
        self.encoding: str = "UTF-8"
        self.retrieved_at: str = _utc_now_iso()
        self.redirect_chain: List[str] = []
        self.history: List[str] = []
        self.history_index: int = -1
        self.session_storage: Dict[str, Dict[str, str]] = {}
        self.network_requests: List[NetworkEventRecord] = []
        self.console_messages: List[str] = []
        self.last_navigation: Optional[NavigationOutcome] = None

    def close(self) -> None:
        self.isolate.close()


class ObscuraBrowserContext:
    """Shared context across tabs: RFC 6265 CookieJar, per-origin localStorage, network policy."""

    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        proxy_url: Optional[str] = None,
        storage_dir: Optional[Path] = None,
        allow_private: bool = False,
        allow_file: bool = False,
        stealth: Optional[StealthProfile] = None,
    ) -> None:
        # The stealth profile is the single source of truth for identity. When
        # it is present its UA wins over the raw override so the header layer
        # and the JavaScript layer cannot describe different machines.
        self.stealth: Optional[StealthProfile] = stealth
        if stealth is not None:
            self.user_agent = stealth.user_agent
        else:
            self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.proxy_url = proxy_url
        self.storage_dir = storage_dir
        self.allow_private = allow_private
        self.allow_file = allow_file
        self.cookie_jar = CookieJar()
        # origin -> {key: value}
        self.local_storage: Dict[str, Dict[str, str]] = {}

        if self.storage_dir is not None:
            cookie_file = self.storage_dir / "cookies.json"
            if cookie_file.is_file():
                self.cookie_jar.load_from_file(cookie_file)
            ls_file = self.storage_dir / "local_storage.json"
            if ls_file.is_file():
                try:
                    data = json.loads(ls_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for origin, entries in data.items():
                            if isinstance(entries, dict):
                                self.local_storage[str(origin)] = {
                                    str(k): str(v) for k, v in entries.items()
                                }
                except Exception:
                    pass

    def persist_storage(self) -> None:
        if self.storage_dir is None:
            return
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_jar.save_to_file(self.storage_dir / "cookies.json")
        ls_path = self.storage_dir / "local_storage.json"
        tmp = ls_path.with_suffix(".json.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(self.local_storage, indent=2).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, ls_path)


def _host_header(parts: Any, port: Optional[int]) -> str:
    """The exact ``Host`` value Chromium would send for a parsed URL."""
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        pass
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if (parts.scheme or "").lower() == "https" else 80
    if port and port != default_port:
        return f"{host}:{port}"
    return host


class _BufferedResponse:
    """A response for URLs that never touch the network (``file://``)."""

    def __init__(self, status: int, headers: Dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self._offset = 0

    def getcode(self) -> int:
        return self.status

    def read(self, amount: int = -1) -> bytes:
        if amount is None or amount < 0:
            chunk = self._body[self._offset:]
            self._offset = len(self._body)
            return chunk
        chunk = self._body[self._offset:self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        return None

    def __enter__(self) -> "_BufferedResponse":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class _ClosingHttpResponse:
    """``http.client`` response that also hangs up its one-shot connection.

    Every request opens its own connection (there is no pool), so closing the
    response must close the socket too; ``HTTPResponse.close`` alone leaves it
    to the garbage collector.
    """

    def __init__(self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse) -> None:
        self._connection = connection
        self._response = response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def __enter__(self) -> "_ClosingHttpResponse":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def read(self, amount: int = -1) -> bytes:
        return self._response.read(amount)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


def _origin_of(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https") or not parsed.netloc:
        return "null"
    return f"{scheme}://{parsed.netloc.lower()}"


def _detect_charset(content_type: str, body: bytes) -> str:
    m = re.search(r"charset=([^\s;\"']+)", content_type or "", re.IGNORECASE)
    if m:
        return m.group(1).strip().upper()
    head = body[:2048].decode("latin-1", errors="ignore")
    m_meta = re.search(r'<meta[^>]+charset=["\']?([a-zA-Z0-9_-]+)', head, re.IGNORECASE)
    if m_meta:
        return m_meta.group(1).strip().upper()
    return "UTF-8"


class ObscuraNativeEngine:
    """Complete in-process / stdio browser engine powered by vendored Obscura."""

    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        proxy_url: Optional[str] = None,
        storage_dir: Optional[Path] = None,
        allow_private: bool = False,
        allow_file: bool = False,
        stealth: bool = False,
        stealth_profile: Optional[StealthProfile] = None,
    ) -> None:
        self._lock = threading.RLock()
        # A profile passed explicitly wins; otherwise `stealth=True` builds the
        # default identity around whatever user agent was requested.
        if stealth_profile is None and stealth:
            from .stealth import profile_for_engine

            stealth_profile = profile_for_engine(stealth=True, user_agent=user_agent)
        self.stealth_profile: Optional[StealthProfile] = stealth_profile
        self.stealth_error: Optional[str] = None
        self.context = ObscuraBrowserContext(
            user_agent=user_agent,
            proxy_url=proxy_url,
            storage_dir=storage_dir,
            allow_private=allow_private,
            allow_file=allow_file,
            stealth=stealth_profile,
        )
        self._tabs: Dict[str, BrowserTab] = {}
        self._tab_counter = 1
        first_id = "tab-1"
        self._tabs[first_id] = BrowserTab(first_id, self.context)
        self._active_tab_id = first_id
        self._closed = False

    # ------------------------------------------------------------- stealth
    def stealth_capability(self) -> Dict[str, Any]:
        """What stealth is doing in this engine instance, for the handshake."""
        return build_stealth_capability(self.stealth_profile, error=self.stealth_error)

    def _stealth_payload(self) -> Optional[Dict[str, Any]]:
        """The identity block handed to the V8 isolate on every navigation."""
        profile = self.stealth_profile
        if profile is None:
            return None
        return {
            "before": profile.identity_globals(),
            "after": profile.post_init_globals(),
        }

    def _request_headers(
        self,
        url: str,
        *,
        resource_type: str,
        referrer: Optional[str],
        initiator: Optional[str],
        method: str,
        cookie: Optional[str],
        content_type: Optional[str],
    ) -> Dict[str, str]:
        """Headers for one request.

        Without a stealth profile this keeps the historical minimal set: adding
        browser-shaped headers only where they were asked for keeps stealth an
        explicit, observable choice.
        """
        profile = self.stealth_profile
        if profile is None:
            headers: Dict[str, str] = {
                "User-Agent": self.context.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            if cookie:
                headers["Cookie"] = cookie
            if content_type:
                headers["Content-Type"] = content_type
            return headers

        if content_type is None and method.upper() not in ("GET", "HEAD"):
            content_type = "application/x-www-form-urlencoded"
        return profile.build_headers(
            url=url,
            resource_type=resource_type,
            referrer=referrer,
            initiator=initiator,
            method=method,
            cookie=cookie,
            content_type=content_type,
        )

    @property
    def active_tab(self) -> BrowserTab:
        tab = self._tabs.get(self._active_tab_id)
        if tab is None:
            raise RuntimeError(f"unknown tab id '{self._active_tab_id}'")
        return tab

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.context.persist_storage()
            for tab in list(self._tabs.values()):
                tab.close()
            self._tabs.clear()

    def _effective_proxy(self, scheme: str, host: str) -> Optional[str]:
        """The proxy for one request: explicit config first, then the environment.

        ``urllib`` used to pick up ``HTTP_PROXY``/``HTTPS_PROXY`` (and
        ``NO_PROXY``) implicitly when the opener was built. The transport is
        explicit now, so the same resolution has to be explicit too.
        """
        if self.context.proxy_url:
            return str(self.context.proxy_url)
        try:
            if urllib.request.proxy_bypass(host):
                return None
            proxies = urllib.request.getproxies()
        except Exception:
            return None
        candidate = proxies.get(scheme) or proxies.get("all")
        return str(candidate) if candidate else None

    def _open_http(
        self,
        url: str,
        *,
        method: str,
        body: Optional[bytes],
        headers: Dict[str, str],
        timeout: float,
    ) -> Union[_BufferedResponse, _ClosingHttpResponse]:
        """Open one HTTP(S) request with the headers exactly as given.

        ``urllib`` cannot be used for this: its handler rewrites every header
        name through ``str.title()`` (turning ``sec-ch-ua`` into ``Sec-Ch-Ua``)
        and force-sets ``Connection: close``. Both are visible on the wire, so
        the request is built on ``http.client`` directly. Redirects, cookies and
        SSRF checks stay in ``_fetch_http``; this method performs exactly one
        hop.
        """
        parts = urllib.parse.urlsplit(url)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname or ""
        if scheme == "file":
            return self._open_file(url, parts)
        if scheme not in ("http", "https") or not host:
            raise PageUnavailableError(
                f"Unsupported URL for the browser engine: {url}",
                detail="unsupported_scheme",
            )
        try:
            explicit_port = parts.port
        except ValueError as exc:
            raise PageUnavailableError(
                f"invalid port in URL: {url}", detail="invalid_url"
            ) from exc
        port = explicit_port or (443 if scheme == "https" else 80)
        selector = parts.path or "/"
        if parts.query:
            selector += "?" + parts.query

        # `Host` first, exactly as Chromium emits it. `http.client` skips its
        # own Host when the caller supplies one, which also keeps proxy requests
        # pointing at the origin host rather than the proxy.
        merged: Dict[str, str] = {"Host": _host_header(parts, explicit_port)}
        merged.update(headers)

        proxy_url = self._effective_proxy(scheme, host)
        proxy = urllib.parse.urlsplit(proxy_url) if proxy_url else None
        proxy_auth: Dict[str, str] = {}
        if proxy is not None and proxy.hostname and proxy.username is not None:
            credentials = (
                urllib.parse.unquote(proxy.username)
                + ":"
                + urllib.parse.unquote(proxy.password or "")
            )
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            proxy_auth["Proxy-Authorization"] = "Basic " + token

        connection: http.client.HTTPConnection
        if scheme == "https":
            context = ssl.create_default_context()
            if proxy is not None and proxy.hostname:
                # CONNECT through the proxy, then TLS to the origin. The proxy
                # credential must not be forwarded down the tunnel.
                connection = http.client.HTTPSConnection(
                    proxy.hostname, proxy.port or 80, timeout=timeout, context=context
                )
                connection.set_tunnel(host, port, headers=proxy_auth)
            else:
                connection = http.client.HTTPSConnection(
                    host, port, timeout=timeout, context=context
                )
            target = selector
        elif proxy is not None and proxy.hostname:
            connection = http.client.HTTPConnection(
                proxy.hostname, proxy.port or 80, timeout=timeout
            )
            # A plain-HTTP proxy wants the absolute URI on the request line.
            target = urllib.parse.urlunsplit((scheme, parts.netloc, selector, "", ""))
            for name, value in proxy_auth.items():
                merged.setdefault(name, value)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
            target = selector

        try:
            connection.request(method, target, body=body, headers=merged)
            return _ClosingHttpResponse(connection, connection.getresponse())
        except BaseException:
            connection.close()
            raise

    def _open_file(self, url: str, parts: Any) -> _BufferedResponse:
        """Read a ``file://`` URL after the caller's policy checks have passed."""
        if not self.context.allow_file:
            raise SecurityBlockedError(
                "file:// navigation is disabled for MCP",
                detail="file_scheme_disabled",
            )
        path = urllib.request.url2pathname(parts.path or "")
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise PageUnavailableError(
                f"could not read {url}: {exc}", detail="file_unreadable"
            ) from exc
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        return _BufferedResponse(200, {"Content-Type": content_type}, data)

    def _fetch_http(
        self,
        url: str,
        *,
        method: str = "GET",
        body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        resource_type: str = "document",
        referrer: Optional[str] = None,
        initiator: Optional[str] = None,
    ) -> Tuple[str, int, str, bytes, List[str]]:
        """Perform an SSRF-guarded HTTP request with manual redirect validation.

        `referrer` is the page the request originates from (used for ``Referer``
        and ``Sec-Fetch-Site``); `initiator` is the document that started a
        navigation when it differs from the referrer. Both only matter in
        stealth mode, where the header layer has to look like a page load.

        Returns `(final_url, status_code, content_type, body_bytes, redirect_chain)`.
        """
        private_ok = allow_private_network(self.context.allow_private)
        current_url, _ = validate_navigation_url(
            url,
            allow_private=private_ok,
            allow_file=self.context.allow_file,
        )

        if current_url.lower() in ("about:blank", "about:srcdoc"):
            return (
                current_url,
                200,
                "text/html; charset=utf-8",
                b"<html><head></head><body></body></html>",
                [current_url],
            )

        req_method = method.upper()
        req_body = body
        # Headers that must survive redirects are built per hop below; this is
        # only the caller-supplied overlay (an explicit Authorization, etc.).
        req_headers: Dict[str, str] = dict(headers or {})

        redirect_chain: List[str] = [current_url]
        deadline = time.monotonic() + max(1.0, float(timeout))

        for hop in range(FETCH_REDIRECT_LIMIT + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationTimeoutError(
                    f"Navigation timeout after {timeout}s",
                    detail="navigation_timeout",
                )

            cookie_hdr = self.context.cookie_jar.cookie_header(current_url)
            hop_headers = self._request_headers(
                current_url,
                resource_type=resource_type,
                referrer=referrer,
                initiator=initiator,
                method=req_method,
                cookie=cookie_hdr or None,
                content_type=(
                    "application/x-www-form-urlencoded" if req_body and req_method not in ("GET", "HEAD") else None
                ),
            )
            # Caller-supplied headers win, and go last so they cannot be
            # reordered behind the identity headers.
            hop_headers.update(req_headers)

            status_code = 200
            resp_headers: Any = {}
            raw_chunks: List[bytes] = []

            try:
                with self._open_http(
                    current_url,
                    method=req_method,
                    body=req_body if req_method not in ("GET", "HEAD") else None,
                    headers=hop_headers,
                    timeout=max(0.5, min(remaining, 30.0)),
                ) as resp:
                    status_code = getattr(resp, "status", None) or resp.getcode() or 200
                    resp_headers = resp.headers
                    total = 0
                    while True:
                        if time.monotonic() > deadline:
                            raise OperationTimeoutError(
                                f"Navigation timeout after {timeout}s",
                                detail="navigation_timeout",
                            )
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_NAVIGATION_BODY_BYTES:
                            raise ExtractionError(
                                f"Response body exceeded {MAX_NAVIGATION_BODY_BYTES} bytes",
                                detail="response_too_large",
                            )
                        raw_chunks.append(chunk)
            except (TimeoutError, socket.timeout) as exc:  # type: ignore[name-defined]
                raise OperationTimeoutError(
                    f"Navigation timeout after {timeout}s: {exc}",
                    detail="navigation_timeout",
                ) from exc
            except ssl.SSLError as exc:
                raise PageUnavailableError(
                    f"TLS error while fetching {current_url}: {exc}",
                    detail="tls_error",
                ) from exc
            except http.client.HTTPException as exc:
                raise PageUnavailableError(
                    f"malformed HTTP response from {current_url}: {exc}",
                    detail="protocol_error",
                ) from exc
            except OSError as exc:
                if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
                    raise OperationTimeoutError(
                        f"Navigation timeout after {timeout}s: {exc}",
                        detail="navigation_timeout",
                    ) from exc
                raise PageUnavailableError(
                    f"Network error: {current_url}: {exc}",
                    detail="network_error",
                ) from exc

            # Store any Set-Cookie headers from this hop
            set_cookies: List[str] = []
            if hasattr(resp_headers, "get_all"):
                set_cookies = resp_headers.get_all("Set-Cookie") or []
            elif hasattr(resp_headers, "getheaders"):
                set_cookies = resp_headers.getheaders("Set-Cookie") or []
            elif isinstance(resp_headers, dict):
                sc = resp_headers.get("Set-Cookie") or resp_headers.get("set-cookie")
                if isinstance(sc, list):
                    set_cookies = sc
                elif isinstance(sc, str):
                    set_cookies = [sc]
            for sc_hdr in set_cookies:
                self.context.cookie_jar.insert_from_header(sc_hdr, current_url, from_http=True)

            # Check for HTTP redirect (301, 302, 303, 307, 308)
            if status_code in (301, 302, 303, 307, 308):
                location = None
                if hasattr(resp_headers, "get"):
                    location = resp_headers.get("Location") or resp_headers.get("location")
                if location:
                    if hop >= FETCH_REDIRECT_LIMIT:
                        raise PageUnavailableError(
                            f"Redirect loop detected (exceeded {FETCH_REDIRECT_LIMIT} redirects)",
                            detail="redirect_loop",
                        )
                    next_url = urllib.parse.urljoin(current_url, location.strip())
                    next_url, _ = validate_navigation_url(
                        next_url,
                        allow_private=private_ok,
                        allow_file=self.context.allow_file,
                    )
                    if _origin_of(next_url) != _origin_of(current_url):
                        for sensitive in ("Authorization", "authorization", "Proxy-Authorization", "Cookie"):
                            req_headers.pop(sensitive, None)
                    if status_code in (301, 302, 303) and req_method == "POST":
                        req_method = "GET"
                        req_body = None
                    current_url = next_url
                    redirect_chain.append(current_url)
                    continue

            content_type = "text/html"
            if hasattr(resp_headers, "get"):
                content_type = resp_headers.get("Content-Type") or resp_headers.get("content-type") or "text/html"

            body_bytes = b"".join(raw_chunks)
            content_encoding = ""
            if hasattr(resp_headers, "get"):
                content_encoding = (
                    resp_headers.get("Content-Encoding")
                    or resp_headers.get("content-encoding")
                    or ""
                )
            if content_encoding:
                try:
                    body_bytes = decode_body(body_bytes, str(content_encoding))
                except ContentEncodingError as exc:
                    raise ExtractionError(
                        f"response encoding could not be decoded: {exc}",
                        detail="content_encoding_unsupported",
                    ) from exc
            self.active_tab.network_requests.append(
                NetworkEventRecord(
                    url=current_url,
                    method=req_method,
                    status=status_code,
                    content_type=content_type,
                    resource_type=resource_type,
                    redirected=len(redirect_chain) > 1,
                    timestamp=_utc_now_iso(),
                )
            )
            return current_url, status_code, content_type, body_bytes, redirect_chain

        raise PageUnavailableError(
            f"Redirect loop detected (exceeded {FETCH_REDIRECT_LIMIT} redirects)",
            detail="redirect_loop",
        )

    def _sync_tab_from_v8_state(self, tab: BrowserTab, v8_state: Dict[str, Any]) -> None:
        if not isinstance(v8_state, dict):
            return
        url = v8_state.get("url") or tab.current_url
        tab.current_url = str(url)
        if "title" in v8_state:
            tab.title = str(v8_state.get("title") or "")

        for raw_cookie in v8_state.get("setCookies") or []:
            self.context.cookie_jar.insert_from_header(
                str(raw_cookie), tab.current_url, from_http=False
            )

        origin = _origin_of(tab.current_url)
        if origin != "null":
            if "localStorage" in v8_state and isinstance(v8_state["localStorage"], list):
                self.context.local_storage[origin] = {
                    str(k): str(v) for k, v in v8_state["localStorage"]
                }
            if "sessionStorage" in v8_state and isinstance(v8_state["sessionStorage"], list):
                tab.session_storage[origin] = {
                    str(k): str(v) for k, v in v8_state["sessionStorage"]
                }

        if "consoleMessages" in v8_state and isinstance(v8_state["consoleMessages"], list):
            tab.console_messages = [str(m) for m in v8_state["consoleMessages"]]

    def _follow_pending_v8_navigation(
        self,
        tab: BrowserTab,
        v8_state: Dict[str, Any],
        *,
        timeout: float = 30.0,
        depth: int = 0,
    ) -> None:
        if depth >= 5 or not isinstance(v8_state, dict):
            return
        pending = v8_state.get("pendingNavigation")
        if not isinstance(pending, dict) or not pending.get("url"):
            return
        target_url = str(pending["url"])
        method = str(pending.get("method") or "GET").upper()
        body_str = str(pending.get("body") or "")
        body_bytes = body_str.encode("utf-8") if method == "POST" else None
        self.navigate(
            target_url,
            timeout=timeout,
            method=method,
            body=body_bytes,
            _record_history=v8_state.get("pendingHistoryIndex") is None,
            _depth=depth + 1,
            _initiator_url=tab.current_url if tab.current_url != "about:blank" else None,
        )

    def navigate(
        self,
        url: str,
        *,
        wait_until: str = "load",
        timeout: float = 30.0,
        method: str = "GET",
        body: Optional[bytes] = None,
        _record_history: bool = True,
        _depth: int = 0,
        _initiator_url: Optional[str] = None,
    ) -> NavigationOutcome:
        with self._lock:
            tab = self.active_tab
            deadline = time.monotonic() + max(0.5, float(timeout))
            # A navigation started from a page (form post, JS redirect) passes
            # its initiator; an address-bar-style navigate sends none, which is
            # what Chromium reports as `Sec-Fetch-Site: none`.
            final_url, status_code, content_type, body_bytes, redirect_chain = self._fetch_http(
                url,
                method=method,
                body=body,
                timeout=timeout,
                resource_type="document",
                initiator=_initiator_url,
            )

            ct_lower = (content_type or "").lower()
            warnings: List[str] = []

            # PDF handling (matching obscura-browser/src/pdf.rs)
            if "application/pdf" in ct_lower or looks_like_pdf(body_bytes):
                text, pdf_info = pdf_to_text(body_bytes)
                escaped_text = (
                    text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
                title = urllib.parse.urlsplit(final_url).path.rsplit("/", 1)[-1] or "PDF Document"
                html = (
                    f"<!DOCTYPE html><html><head><title>{title}</title></head>"
                    f"<body><main><pre>{escaped_text}</pre></main></body></html>"
                )
                encoding = "UTF-8"
            else:
                encoding = _detect_charset(content_type, body_bytes)
                try:
                    html = body_bytes.decode(encoding, errors="replace")
                except LookupError:
                    encoding = "UTF-8"
                    html = body_bytes.decode("utf-8", errors="replace")

            # Pre-fetch external <script src="..."> subresources when within budget
            external_scripts: Dict[str, str] = {}
            if wait_until != "none":
                script_srcs = re.findall(
                    r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>',
                    html,
                    flags=re.IGNORECASE,
                )
                for raw_src in script_srcs[:MAX_SUBRESOURCE_SCRIPTS]:
                    rem = deadline - time.monotonic()
                    if rem <= 0.2:
                        warnings.append("subresource_script_deadline_reached")
                        break
                    resolved_src = urllib.parse.urljoin(final_url, raw_src.strip())
                    try:
                        _, s_status, _, s_bytes, _ = self._fetch_http(
                            resolved_src,
                            timeout=min(rem, 5.0),
                            resource_type="script",
                            referrer=final_url,
                        )
                        if 200 <= s_status < 300 and len(s_bytes) <= MAX_SUBRESOURCE_SCRIPT_BYTES:
                            external_scripts[resolved_src] = s_bytes.decode("utf-8", errors="replace")
                            external_scripts[raw_src] = external_scripts[resolved_src]
                    except SecurityBlockedError:
                        warnings.append(f"blocked_subresource:{resolved_src}")
                    except Exception:
                        pass

            if _record_history and final_url != "about:blank":
                if tab.history_index < len(tab.history) - 1:
                    tab.history = tab.history[: tab.history_index + 1]
                tab.history.append(final_url)
                tab.history_index = len(tab.history) - 1

            origin = _origin_of(final_url)
            local_entries = list((self.context.local_storage.get(origin) or {}).items())
            session_entries = list((tab.session_storage.get(origin) or {}).items())
            cookie_js = self.context.cookie_jar.cookie_header_for_js(final_url)

            load_payload = {
                "url": final_url,
                "referrer": tab.current_url if tab.current_url != "about:blank" else "",
                "encoding": encoding,
                "contentType": content_type.split(";")[0].strip() or "text/html",
                "scriptingEnabled": True,
                "cookieString": cookie_js,
                "localStorage": local_entries,
                "sessionStorage": session_entries,
                "sessionHistory": {
                    "start": 0,
                    "current": max(0, tab.history_index),
                    "urls": tab.history or [final_url],
                },
                "html": html,
                "externalScripts": external_scripts,
            }
            stealth_payload = self._stealth_payload()
            if stealth_payload is not None:
                load_payload["stealth"] = stealth_payload

            rem_v8 = max(0.2, deadline - time.monotonic())
            raw_v8 = tab.isolate.eval(
                f"__iralens_load_document({json.dumps(json.dumps(load_payload))})",
                timeout_sec=min(rem_v8, DEFAULT_V8_TIMEOUT_SEC),
            )
            v8_state = json.loads(raw_v8) if isinstance(raw_v8, str) else (raw_v8 or {})
            self._sync_tab_from_v8_state(tab, v8_state)

            tab.requested_url = url if _depth == 0 else tab.requested_url
            tab.current_url = final_url
            tab.status_code = status_code
            tab.content_type = content_type
            tab.encoding = encoding
            tab.retrieved_at = _utc_now_iso()
            if _depth == 0:
                tab.redirect_chain = list(redirect_chain)
            else:
                tab.redirect_chain.extend(redirect_chain[1:] if len(redirect_chain) > 1 else redirect_chain)

            # Follow client-side JS redirect if triggered during page load
            if v8_state.get("pendingNavigation") and _depth < 5:
                self._follow_pending_v8_navigation(
                    tab,
                    v8_state,
                    timeout=max(0.5, deadline - time.monotonic()),
                    depth=_depth,
                )
                assert tab.last_navigation is not None
                return tab.last_navigation

            ok = 200 <= status_code < 400
            err_code = f"http_{status_code}" if not ok else None
            outcome = NavigationOutcome(
                requested_url=tab.requested_url,
                final_url=tab.current_url,
                title=tab.title,
                status_code=status_code,
                content_type=content_type,
                redirected=len(tab.redirect_chain) > 1 or tab.current_url != tab.requested_url,
                redirect_chain=list(tab.redirect_chain),
                retrieved_at=tab.retrieved_at,
                encoding=encoding,
                ok=ok,
                error=err_code,
                warnings=warnings,
            )
            tab.last_navigation = outcome
            self.context.persist_storage()
            return outcome

    def call_v8_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        *,
        timeout_sec: float = DEFAULT_V8_TIMEOUT_SEC,
    ) -> Any:
        with self._lock:
            tab = self.active_tab
            args_json = json.dumps(args or {})
            raw = tab.isolate.eval(
                f"__iralens_mcp_call({json.dumps(tool_name)}, {json.dumps(args_json)})",
                timeout_sec=timeout_sec,
            )
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                v8_state = parsed.get("state")
                if isinstance(v8_state, dict):
                    self._sync_tab_from_v8_state(tab, v8_state)
                    if v8_state.get("pendingNavigation"):
                        self._follow_pending_v8_navigation(tab, v8_state, timeout=timeout_sec)
                self.context.persist_storage()
                if "text" in parsed:
                    return parsed["text"]
                if "data" in parsed:
                    return parsed["data"]
            return parsed

    def dispatch_tool(self, name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """Dispatch any of the 30+ MCP `browser_*` tools matching `obscura-mcp`."""
        params = dict(args or {})
        timeout = float(params.get("timeout") or DEFAULT_V8_TIMEOUT_SEC)

        with self._lock:
            if name == "browser_navigate":
                url = str(params.get("url") or "").strip()
                if not url:
                    raise ValueError("Missing 'url' parameter")
                wait_until = str(params.get("wait_until") or params.get("waitUntil") or "load")
                outcome = self.navigate(url, wait_until=wait_until, timeout=timeout)
                return outcome.to_dict()

            if name in (
                "browser_snapshot",
                "browser_markdown",
                "browser_links",
                "browser_interactive_elements",
                "browser_click",
                "browser_fill",
                "browser_type",
                "browser_press_key",
                "browser_select_option",
                "browser_detect_forms",
                "browser_fill_form",
                "browser_extract",
                "browser_get_attribute",
                "browser_count",
                "browser_search",
                "browser_scroll",
                "browser_evaluate",
            ):
                return self.call_v8_tool(name, params, timeout_sec=timeout)

            if name == "browser_wait_for":
                sel = params.get("selector")
                raw_t = float(params.get("timeout") or 5.0)
                budget = raw_t / 1000.0 if raw_t > 120 else raw_t
                if not sel:
                    time.sleep(min(budget, 2.0))
                    return "Waited"
                deadline = time.monotonic() + max(0.1, budget)
                while time.monotonic() < deadline:
                    res = self.call_v8_tool("browser_count", {"selector": sel}, timeout_sec=2.0)
                    if isinstance(res, dict) and int(res.get("count", 0)) > 0:
                        return f"Element matching '{sel}' is present"
                    time.sleep(0.05)
                raise OperationTimeoutError(
                    f"Navigation timeout waiting for selector '{sel}'",
                    detail="wait_for_timeout",
                )

            if name == "browser_wait_for_text":
                text = str(params.get("text") or "")
                if not text:
                    raise ValueError("Missing 'text' parameter")
                raw_t = float(params.get("timeout") or 5.0)
                budget = raw_t / 1000.0 if raw_t > 120 else raw_t
                deadline = time.monotonic() + max(0.1, budget)
                while time.monotonic() < deadline:
                    res = self.call_v8_tool(
                        "browser_search", {"query": text, "context_chars": 40}, timeout_sec=2.0
                    )
                    if isinstance(res, dict) and int(res.get("count", 0)) > 0:
                        return f"Found text '{text}'"
                    time.sleep(0.05)
                raise OperationTimeoutError(
                    f"Navigation timeout waiting for text '{text}'",
                    detail="wait_for_text_timeout",
                )

            if name == "browser_wait_for_function":
                expr = str(params.get("expression") or "")
                if not expr:
                    raise ValueError("Missing 'expression' parameter")
                raw_t = float(params.get("timeout") or 5.0)
                budget = raw_t / 1000.0 if raw_t > 120 else raw_t
                deadline = time.monotonic() + max(0.1, budget)
                while time.monotonic() < deadline:
                    val = self.call_v8_tool("browser_evaluate", {"expression": expr}, timeout_sec=2.0)
                    if val:
                        return val
                    time.sleep(0.05)
                raise OperationTimeoutError(
                    f"Navigation timeout waiting for function '{expr}'",
                    detail="wait_for_function_timeout",
                )

            if name == "browser_back":
                tab = self.active_tab
                if tab.history_index <= 0:
                    raise RuntimeError("No previous page in history.")
                tab.history_index -= 1
                target = tab.history[tab.history_index]
                return self.navigate(target, timeout=timeout, _record_history=False).to_dict()

            if name == "browser_forward":
                tab = self.active_tab
                if tab.history_index < 0 or tab.history_index >= len(tab.history) - 1:
                    raise RuntimeError("No forward page in history.")
                tab.history_index += 1
                target = tab.history[tab.history_index]
                return self.navigate(target, timeout=timeout, _record_history=False).to_dict()

            if name == "browser_reload":
                tab = self.active_tab
                if not tab.history or tab.current_url == "about:blank":
                    raise RuntimeError("Nothing to reload.")
                return self.navigate(tab.current_url, timeout=timeout, _record_history=False).to_dict()

            if name == "browser_get_cookies":
                tab = self.active_tab
                req_url = str(params.get("url") or "") or (
                    tab.current_url if tab.current_url != "about:blank" else ""
                )
                return self.context.cookie_jar.all_cookies(req_url or None)

            if name == "browser_set_cookie":
                c_name = str(params.get("name") or "").strip()
                c_val = str(params.get("value") or "")
                if not c_name:
                    raise ValueError("Missing cookie 'name'")
                tab = self.active_tab
                domain = str(params.get("domain") or "").strip()
                if not domain and tab.current_url != "about:blank":
                    domain = urllib.parse.urlsplit(tab.current_url).hostname or ""
                if not domain:
                    raise ValueError("Cookie 'domain' is required when no page is loaded")
                path = str(params.get("path") or "/")
                secure = bool(params.get("secure", False))
                http_only = bool(params.get("http_only", False))
                same_site = str(params.get("same_site") or "Lax")
                expires = int(params["expires"]) if params.get("expires") is not None else None
                self.context.cookie_jar.set_cookie(
                    c_name,
                    c_val,
                    domain=domain,
                    path=path,
                    secure=secure,
                    http_only=http_only,
                    same_site=same_site,
                    expires=expires,
                )
                # Refresh V8 cookieString if on matching page
                if tab.current_url != "about:blank":
                    js_cookies = self.context.cookie_jar.cookie_header_for_js(tab.current_url)
                    tab.isolate.eval(
                        f"__iralens_host_state.cookieString = {json.dumps(js_cookies)};"
                    )
                self.context.persist_storage()
                return {"status": "ok", "name": c_name, "domain": domain}

            if name == "browser_clear_cookies":
                self.context.cookie_jar.clear()
                for t in self._tabs.values():
                    t.isolate.eval("__iralens_host_state.cookieString = '';")
                self.context.persist_storage()
                return "Cookies cleared."

            if name == "browser_storage_state":
                return {
                    "cookies": self.context.cookie_jar.all_cookies(),
                    "origins": [
                        {
                            "origin": origin,
                            "localStorage": [
                                {"name": k, "value": v} for k, v in entries.items()
                            ],
                        }
                        for origin, entries in self.context.local_storage.items()
                    ],
                }

            if name == "browser_set_storage_state":
                raw_state = params.get("state")
                state_obj = (
                    json.loads(raw_state)
                    if isinstance(raw_state, str)
                    else (raw_state if isinstance(raw_state, dict) else params)
                )
                if isinstance(state_obj, dict):
                    cookies = state_obj.get("cookies")
                    if isinstance(cookies, list):
                        self.context.cookie_jar.load_dicts(cookies)
                    origins = state_obj.get("origins")
                    if isinstance(origins, list):
                        for item in origins:
                            if not isinstance(item, dict):
                                continue
                            origin = str(item.get("origin") or "")
                            ls_list = item.get("localStorage")
                            if origin and isinstance(ls_list, list):
                                self.context.local_storage[origin] = {
                                    str(entry.get("name", "")): str(entry.get("value", ""))
                                    for entry in ls_list
                                    if isinstance(entry, dict) and entry.get("name")
                                }
                            elif origin and isinstance(ls_list, dict):
                                self.context.local_storage[origin] = {
                                    str(k): str(v) for k, v in ls_list.items()
                                }
                self.context.persist_storage()
                return {"status": "ok"}

            if name == "browser_tab_new":
                self._tab_counter += 1
                new_id = f"tab-{self._tab_counter}"
                new_tab = BrowserTab(new_id, self.context)
                self._tabs[new_id] = new_tab
                self._active_tab_id = new_id
                url = str(params.get("url") or "").strip()
                if url:
                    self.navigate(url, timeout=timeout)
                return {
                    "tab_id": new_id,
                    "url": new_tab.current_url,
                    "title": new_tab.title,
                }

            if name == "browser_tab_list":
                return [
                    {
                        "tab_id": tid,
                        "url": t.current_url,
                        "title": t.title,
                        "active": tid == self._active_tab_id,
                    }
                    for tid, t in self._tabs.items()
                ]

            if name == "browser_tab_switch":
                target_id = str(params.get("tab_id") or "")
                if target_id not in self._tabs:
                    raise RuntimeError(f"unknown tab id '{target_id}'")
                self._active_tab_id = target_id
                tab = self._tabs[target_id]
                return {"tab_id": target_id, "url": tab.current_url, "title": tab.title}

            if name == "browser_tab_close":
                target_id = str(params.get("tab_id") or self._active_tab_id)
                if target_id not in self._tabs:
                    raise RuntimeError(f"unknown tab id '{target_id}'")
                if len(self._tabs) <= 1:
                    raise RuntimeError("Cannot close the last remaining tab.")
                closed = self._tabs.pop(target_id)
                closed.close()
                if self._active_tab_id == target_id:
                    self._active_tab_id = next(iter(self._tabs.keys()))
                return {"closed": target_id, "active_tab_id": self._active_tab_id}

            if name == "browser_network_requests":
                return [
                    {
                        "url": r.url,
                        "method": r.method,
                        "status": r.status,
                        "content_type": r.content_type,
                        "resource_type": r.resource_type,
                        "redirected": r.redirected,
                        "timestamp": r.timestamp,
                    }
                    for r in self.active_tab.network_requests
                ]

            if name == "browser_console_messages":
                return list(self.active_tab.console_messages)

            if name == "browser_close":
                self.close()
                return "Browser closed."

            raise ValueError(f"Unknown tool: {name}")
