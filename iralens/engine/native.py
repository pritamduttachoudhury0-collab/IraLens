# -*- coding: utf-8 -*-
"""Browser engine driver (internal implementation detail).

Spawns the native headless engine binary and speaks its line-delimited
JSON-RPC protocol over stdio. Everything above this module sees only the
unified IraLens surface: Python values, `Artifact`s, and unified errors.
Engine messages are classified into the shared error taxonomy and sanitized
so no internal component identity ever leaks to the AI-facing layer.
"""

from __future__ import annotations

import base64
import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import errors
from ..proc import utf8_env
from ..security import public_message
from .._internal.browser_engine.mcp_server import (
    CAPTCHA_KEY_ENV_VAR,
    CAPTCHA_PROVIDER_ENV_VAR,
    PROXY_ENV_VAR,
)
from .locate import find_engine

_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 60
#: Budget for the JSON-RPC `initialize` handshake. The browser itself starts on
#: the first navigation, so a handshake that takes longer than this means the
#: binary is stuck (not merely slow). Override with the
#: `engine_startup_timeout_seconds` config key / IRALENS_ENGINE_STARTUP_TIMEOUT_SECONDS.
_HANDSHAKE_TIMEOUT = 20


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def first_line_of(text: str) -> str:
    """First line of a multi-line engine message (V8 stacks are long)."""
    lines = str(text or "").splitlines()
    return lines[0].strip() if lines else ""


def classify_engine_error(text: str) -> errors.IraLensError:
    """Map engine failure text onto the unified error taxonomy."""
    text = str(text)
    while text.startswith("Error: "):
        text = text[len("Error: "):]
    low = text.lower()
    msg = public_message(text)

    if "file://" in low and ("disabled" in low or "not allowed" in low):
        return errors.SecurityBlockedError("local file navigation is not allowed", detail=msg)
    if (
        "ssrf" in low
        or "private/reserved ip" in low
        or "private ip" in low
        or "loopback" in low
        or "link-local" in low
        or "cloud metadata" in low
        or ("scheme" in low and "not allowed" in low)
    ):
        return errors.SecurityBlockedError("URL rejected by the browser security policy", detail=msg)
    if "timeout" in low or "timed out" in low:
        return errors.OperationTimeoutError("browser operation timed out", detail=msg)
    if "no previous page in history" in low or "no forward page in history" in low:
        return errors.SessionStateError("no such entry in browser navigation history")
    if "nothing to reload" in low:
        return errors.SessionStateError("no page is loaded to reload")
    if re.search(r"unknown (tab|tool)|no such tab|tab .+ not found", low):
        return errors.SessionStateError("unknown browser tab", hint="call tab_list for valid ids")
    if re.search(r"missing '(ref|selector)'|missing tool name", low):
        return errors.ExtractionError(msg)
    if re.search(r"not found|no element|no match|does not match|no nodes? ", low):
        return errors.ExtractionError(msg)
    if low.startswith("verificationrequired"):
        # The browser tier detected a verification page and could not get
        # past it. That is its own error type: switching transport or
        # retrying will not complete an interactive check. (Checked before
        # the generic auth/captcha rule below, which it would otherwise
        # match.)
        return errors.VerificationRequiredError(
            "the site required interactive verification (captcha or bot check)",
            detail=msg,
        )
    if re.search(r"auth|login|sign[- ]?in|403|401|forbidden|captcha|challenge|blocked by", low):
        return errors.AuthRequiredError(
            "the site rejected anonymous access (login, captcha, or bot check)", detail=msg
        )
    if re.search(
        r"net::|network error|dns|resolve|connection|unreachable|failed to (fetch|load)"
        r"|err_|not found \(404\)|404",
        low,
    ):
        return errors.PageUnavailableError("could not reach the page", detail=msg)
    if re.search(r"navigat", low):
        return errors.NavigationError(msg)
    # A JavaScript failure is an extraction failure with a cause, not an
    # untyped error envelope: the caller needs to know the content could
    # not be produced and that a different transport may still work.
    if ("js error" in low
            or re.search(r"is not a function|is not defined|referenceerror|"
                         r"typeerror|syntaxerror|unhandled (?:promise )?exception",
                         low)):
        first_line = (text.splitlines() or [text])[0]
        return errors.ExtractionError(
            "the page's JavaScript failed while the browser was rendering it",
            hint="the site's script is not supported by the engine; the static "
                 "reader may still be able to read the page",
            detail=first_line,
        )
    # Every failure that leaves the engine is typed. An untyped `error`
    # envelope gave a programmatic caller nothing to branch on.
    return errors.ExtractionError(
        "the browser engine failed to produce the requested content",
        detail=first_line_of(text) or msg,
    )


def _as_list(result: Any) -> Any:
    """Normalize JSON-lines tools that may return a single object."""
    if isinstance(result, dict):
        return [result]
    return result


# ---------------------------------------------------------------------------
# Engine driver
# ---------------------------------------------------------------------------

class EngineError(RuntimeError):
    """Raw engine-level failure before classification."""


class BrowserEngine:
    """Handle on the shared browser engine (lazy, one subprocess)."""

    def __init__(self, config=None, session=None) -> None:
        self.config = config
        self.session = session
        self._proc: Optional[subprocess.Popen] = None
        self._next_id = 0
        self._responses: "queue.Queue[dict]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._stderr_tail: List[str] = []
        self._lock = threading.Lock()
        self._restored_state = False
        self._startup_failure: Optional[errors.EngineUnavailableError] = None
        #: What the running engine reported in its `initialize` response.
        #: Empty until the handshake completes.
        self.capabilities: Dict[str, Any] = {}
        #: Diagnostics for optional features that were asked for but are not
        #: available in this build. Never silent, never fatal.
        self.capability_warnings: List[str] = []

    # ------------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def binary_path(self) -> Path:
        path = find_engine(self.config)
        if not path:
            raise errors.EngineUnavailableError(
                "the browser engine is not installed",
                hint="run: iralens install-engine",
            )
        return path

    def _handshake_timeout(self) -> int:
        raw = self.config.get("engine_startup_timeout_seconds") if self.config is not None else None
        if raw not in (None, ""):
            try:
                return max(1, int(float(raw)))
            except (TypeError, ValueError):
                pass
        return _HANDSHAKE_TIMEOUT

    def start(self) -> None:
        if self.running:
            return
        if self._startup_failure is not None:
            # Remembered for this process only: a binary that just died will not
            # start working on the next call, and respawning it per attempt
            # multiplies the failure cost across every engine and query.
            previous = self._startup_failure
            raise errors.EngineUnavailableError(previous.message, hint=previous.hint,
                                                detail=previous.detail)
        binary = self.binary_path()
        argv = [str(binary), "mcp"]
        # Secrets and per-child settings travel in the environment, never on
        # argv: the command line of a running process is world-readable.
        child_extra: Dict[str, str] = {}
        if self.config is not None:
            if str(self.config.get("stealth", "")).lower() in ("1", "true", "yes"):
                argv.append("--stealth")
            proxy = self.config.get("proxy")
            if proxy:
                child_extra[PROXY_ENV_VAR] = str(proxy)
            # The solving service and its key travel in the environment for
            # the same reason the proxy does: argv is world-readable.
            captcha_provider = self.config.get("captcha_provider")
            if captcha_provider:
                child_extra[CAPTCHA_PROVIDER_ENV_VAR] = str(captcha_provider)
            captcha_key = self.config.get("captcha_api_key")
            if captcha_key:
                child_extra[CAPTCHA_KEY_ENV_VAR] = str(captcha_key)
            ua = self.config.get("user_agent")
            if ua:
                argv += ["--user-agent", str(ua)]
            storage_dir = self.config.get("storage_dir")
            if storage_dir:
                argv += ["--storage-dir", str(storage_dir)]
            if str(self.config.get("allow_private", "")).lower() in ("1", "true", "yes"):
                argv.append("--allow-private")
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=utf8_env(extra=child_extra),
            )
        except OSError as exc:
            raise errors.EngineUnavailableError(
                "could not start the browser engine", detail=public_message(exc)
            ) from exc
        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._reader.start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        try:
            handshake = self._rpc("initialize", {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "iralens", "version": "0.3.5"},
            }, timeout=self._handshake_timeout())
            self._record_capabilities(handshake)
        except errors.EngineUnavailableError as exc:
            self._startup_failure = exc
            self._terminate()
            raise
        self._restore_session_state()

    # ---------------------------------------------------------- capabilities
    def _record_capabilities(self, handshake: Any) -> None:
        """Remember what the engine says it supports.

        The engine is the authority here, not this driver: the same driver
        code talks to a bundled Python-tier engine today and could talk to a
        compiled engine tomorrow, so support is discovered rather than
        assumed from the config that was requested.
        """
        if isinstance(handshake, dict):
            reported = handshake.get("engineCapabilities")
            if isinstance(reported, dict):
                self.capabilities = reported
        stealth = self.capabilities.get("stealth")
        if isinstance(stealth, dict) and stealth.get("requested") and not stealth.get("applied"):
            reason = str(stealth.get("reason") or "not supported by this engine build")
            self.capability_warnings.append(
                "stealth was requested but is not active: " + reason
            )

    def stealth_status(self) -> Dict[str, Any]:
        """Whether stealth is active, and why not, for `doctor` output."""
        stealth = self.capabilities.get("stealth")
        if not isinstance(stealth, dict):
            return {"requested": self._wants_stealth(), "supported": None,
                    "active": False,
                    "note": "the engine did not report capabilities"}
        status = {
            "requested": bool(stealth.get("requested")),
            "supported": bool(stealth.get("supported")),
            "active": bool(stealth.get("applied")),
            "note": str(stealth.get("reason") or ""),
        }
        tls = self.capabilities.get("tls_impersonation")
        if isinstance(tls, dict):
            status["tls_impersonation"] = bool(tls.get("supported"))
        solver = self.capabilities.get("captcha_solver")
        if isinstance(solver, dict):
            status["captcha_solver"] = bool(solver.get("supported"))
            status["captcha_provider"] = str(solver.get("provider") or "")
        return status

    def _wants_stealth(self) -> bool:
        if self.config is None:
            return False
        return str(self.config.get("stealth", "")).lower() in ("1", "true", "yes")

    def _terminate(self) -> None:
        """Drop the child process without persisting anything (failure path)."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def close(self, *, persist_state: bool = True) -> None:
        self._startup_failure = None
        if not self._proc:
            return
        if persist_state:
            try:
                state = self.storage_state()
                if isinstance(state, dict) and state:
                    if self.session is not None:
                        self.session.save_engine_state(state)
            except Exception:
                pass
        try:
            self.call("browser_close", {}, timeout=5)
        except Exception:
            pass
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        self._proc = None

    # ------------------------------------------------------------ JSON-RPC
    def _pump_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._responses.put(payload)

    def _pump_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr_tail.append(line.rstrip())
            if len(self._stderr_tail) > 40:
                self._stderr_tail.pop(0)

    def _engine_exited_error(self, method: str) -> errors.EngineUnavailableError:
        """Typed failure for an engine that died instead of answering.

        A binary that cannot start at all — a dangling `iralens-engine` shim
        after the cache was cleared, a wrong-architecture build, a missing
        shared library — used to make every browser-backed call wait out the
        full RPC timeout (60 s), which is exactly the kind of silent, slow
        failure the reliability contract forbids. The subprocess is polled
        while waiting, so the failure is reported as soon as the process is
        gone, with its exit code and (sanitized) last words.
        """
        code = self._proc.returncode if self._proc else None
        detail = f"{method}: engine process exited (exit {code})"
        tail = " | ".join(line for line in self._stderr_tail[-3:] if line.strip())
        if tail:
            detail = f"{detail}: {public_message(tail)[:200]}"
        return errors.EngineUnavailableError(
            "the browser engine exited before answering",
            hint="reinstall it (iralens install-engine --force) or set IRALENS_ENGINE_PATH "
                 "to a working binary",
            detail=detail,
        )

    def _rpc(self, method: str, params: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT) -> Any:
        if not self.running:
            raise errors.EngineUnavailableError(
                "the browser engine is not running", hint="the engine stopped unexpectedly"
            )
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            request = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                request["params"] = params
            assert self._proc and self._proc.stdin
            try:
                self._proc.stdin.write(json.dumps(request) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise self._engine_exited_error(method) from exc

            deadline = time.monotonic() + max(0.1, timeout)
            # Poll in short slices: a dead child must be detected immediately,
            # not after the whole timeout.
            while True:
                slice_timeout = max(0.05, min(1.0, deadline - time.monotonic()))
                try:
                    payload = self._responses.get(timeout=slice_timeout)
                except queue.Empty:
                    if not self.running:
                        raise self._engine_exited_error(method) from None
                    if time.monotonic() >= deadline:
                        raise errors.OperationTimeoutError(
                            f"browser engine did not respond within {timeout}s"
                        ) from None
                    continue
                if payload.get("id") != req_id:
                    continue  # notification or stale frame
                if "error" in payload:
                    err = payload["error"] or {}
                    raise classify_engine_error(str(err.get("message", "engine error")))
                return payload.get("result")

    def call(self, tool: str, args: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT) -> Any:
        """Invoke one engine tool; returns parsed text JSON when applicable."""
        self.start()
        result = self._rpc("tools/call", {"name": tool, "arguments": args or {}}, timeout=timeout)
        return self._unwrap(result, tool)

    @staticmethod
    def _unwrap(result: Any, tool: str) -> Any:
        if not isinstance(result, dict):
            return result
        if result.get("isError"):
            text = ""
            for block in result.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "")
            raise classify_engine_error(text or f"{tool} failed")
        content = result.get("content")
        if isinstance(content, list):
            texts = []
            media = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") in ("image", "resource"):
                    media.append(block)
            if media:
                return media if len(media) > 1 else media[0]
            joined = "\n".join(texts)
            stripped = joined.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    pass
            if tool == "browser_evaluate" and (
                stripped in ("true", "false", "null")
                or re.fullmatch(r"-?\d+(?:\.\d+)?", stripped) is not None
            ):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    pass
            # JSON-lines payloads (e.g. links) → list of dicts
            lines = [ln for ln in stripped.splitlines() if ln.strip().startswith("{")]
            if lines and len(lines) == len([ln for ln in stripped.splitlines() if ln.strip()]):
                try:
                    return [json.loads(ln) for ln in lines]
                except json.JSONDecodeError:
                    pass
            return joined
        return result

    # --------------------------------------------------------- media helpers
    @staticmethod
    def _media_bytes(block: dict) -> bytes:
        data = block.get("data")
        if data is None and isinstance(block.get("resource"), dict):
            data = block["resource"].get("blob") or block["resource"].get("data")
        if not data:
            raise errors.ExtractionError("engine returned media without payload")
        return base64.b64decode(data)

    def save_media(self, block: dict, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._media_bytes(block))
        return path

    # --------------------------------------------------- session persistence
    def _restore_session_state(self) -> None:
        if self._restored_state or self.session is None:
            self._restored_state = True
            return
        state = self.session.load_engine_state()
        if state:
            try:
                self.call("browser_set_storage_state", {"state": state}, timeout=15)
            except Exception:
                pass  # stale state is not fatal
        self._restored_state = True

    # ------------------------------------------------- high-level operations
    def navigate(self, url: str, wait_until: str = "load", timeout: int = _DEFAULT_TIMEOUT) -> Any:
        args: Dict[str, Any] = {"url": url, "waitUntil": wait_until, "timeout": timeout}
        return self.call("browser_navigate", args, timeout=timeout + 5)

    def snapshot(self, max_chars: int = 4000) -> Any:
        return self.call("browser_snapshot", {"max_chars": max_chars})

    def markdown(self, max_chars: int = 4000) -> Any:
        return self.call("browser_markdown", {"max_chars": max_chars})

    def links(self, limit: int = 100, internal_only: bool = False) -> Any:
        result = self.call("browser_links", {"limit": limit, "internal_only": internal_only})
        return _as_list(result)

    def back(self) -> Any:
        return self.call("browser_back", {})

    def forward(self) -> Any:
        return self.call("browser_forward", {})

    def reload(self) -> Any:
        return self.call("browser_reload", {})

    def click(self, selector: str) -> Any:
        if selector.startswith("ref="):
            return self.call("browser_click", {"ref": selector[4:]})
        return self.call("browser_click", {"selector": selector})

    def fill(self, selector: str, value: str) -> Any:
        if selector.startswith("ref="):
            return self.call("browser_fill", {"ref": selector[4:], "value": value})
        return self.call("browser_fill", {"selector": selector, "value": value})

    def type_text(self, text: str, selector: Optional[str] = None) -> Any:
        args: Dict[str, Any] = {"text": text}
        if selector:
            args["selector"] = selector
        return self.call("browser_type", args)

    def press_key(self, key: str, selector: Optional[str] = None) -> Any:
        args: Dict[str, Any] = {"key": key}
        if selector:
            args["selector"] = selector
        return self.call("browser_press_key", args)

    def select_option(self, selector: str, value: str) -> Any:
        return self.call("browser_select_option", {"selector": selector, "value": value})

    def scroll(self, direction: str = "down", amount: Optional[int] = None, selector: Optional[str] = None) -> Any:
        args: Dict[str, Any] = {"direction": direction}
        if amount is not None:
            args["amount"] = amount
        if selector:
            args["selector"] = selector
        return self.call("browser_scroll", args)

    def evaluate_js(self, expression: str) -> Any:
        return self.call("browser_evaluate", {"expression": expression})

    def wait_for(self, selector: str, timeout: int = 30) -> Any:
        return self.call("browser_wait_for", {"selector": selector, "timeout": timeout}, timeout=timeout + 10)

    def wait_for_text(self, text: str, timeout: int = 30) -> Any:
        return self.call("browser_wait_for_text", {"text": text, "timeout": timeout}, timeout=timeout + 10)

    def find_in_page(self, query: str, case_sensitive: bool = False) -> Any:
        return self.call("browser_search", {"query": query, "case_sensitive": case_sensitive})

    def forms_detect(self) -> Any:
        return self.call("browser_detect_forms", {})

    def forms_fill(self, values: Dict[str, str]) -> Any:
        fields = [{"selector": selector, "value": value} for selector, value in values.items()]
        return self.call("browser_fill_form", {"fields": fields})

    def extract(self, schema: Dict[str, str]) -> Any:
        return self.call("browser_extract", {"schema": schema})

    def count(self, selector: str) -> Any:
        return self.call("browser_count", {"selector": selector})

    def attribute(self, selector: str, name: str) -> Any:
        return self.call("browser_get_attribute", {"selector": selector, "attribute": name})

    def interactive_elements(self) -> Any:
        return self.call("browser_interactive_elements", {})

    def screenshot(self, width: Optional[int] = None, height: Optional[int] = None) -> Any:
        args: Dict[str, Any] = {}
        if width:
            args["width"] = width
        if height:
            args["height"] = height
        return self.call("browser_screenshot", args, timeout=90)

    def pdf(self, landscape: bool = False, print_background: bool = True) -> Any:
        return self.call(
            "browser_pdf",
            {"landscape": landscape, "print_background": print_background},
            timeout=90,
        )

    def cookies_get(self) -> Any:
        return _as_list(self.call("browser_get_cookies", {}))

    def cookies_set(self, name: str, value: str, domain: str = "", path: str = "/") -> Any:
        args: Dict[str, Any] = {"name": name, "value": value}
        if domain:
            args["domain"] = domain
        if path:
            args["path"] = path
        return self.call("browser_set_cookie", args)

    def cookies_clear(self) -> Any:
        return self.call("browser_clear_cookies", {})

    def storage_state(self) -> Any:
        return self.call("browser_storage_state", {})

    def set_storage_state(self, state: Dict[str, Any]) -> Any:
        return self.call("browser_set_storage_state", {"state": state})

    def network_log(self) -> Any:
        return self.call("browser_network_requests", {})

    def console_log(self) -> Any:
        return self.call("browser_console_messages", {})

    def tab_new(self, url: Optional[str] = None) -> Any:
        args: Dict[str, Any] = {}
        if url:
            args["url"] = url
        return self.call("browser_tab_new", args, timeout=_DEFAULT_TIMEOUT)

    def tab_list(self) -> Any:
        return _as_list(self.call("browser_tab_list", {}))

    def tab_switch(self, tab_id: str) -> Any:
        return self.call("browser_tab_switch", {"tab_id": tab_id})

    def tab_close(self, tab_id: str) -> Any:
        return self.call("browser_tab_close", {"tab_id": tab_id})

    def current_url_title(self) -> Dict[str, str]:
        """Best-effort current (url, title) via a DOM read."""
        try:
            data = self.evaluate_js("JSON.stringify({url: location.href, title: document.title})")
            if isinstance(data, str):
                return json.loads(data)
            if isinstance(data, dict):
                return {"url": data.get("url", ""), "title": data.get("title", "")}
        except Exception:
            pass
        return {"url": "", "title": ""}

    def health(self) -> Dict[str, Any]:
        """Non-destructive engine availability check for doctor.

        Output is AI-facing: it never contains binary paths or internal
        component names.
        """
        path = find_engine(self.config)
        if not path:
            return {
                "status": "off",
                "message": "browser engine not installed — run: iralens install-engine",
            }
        if self.running:
            health: Dict[str, Any] = {"status": "ok", "message": "browser engine running"}
            # An optional feature that was asked for but is not active is a
            # correctness issue for the caller, not a cosmetic one: it must be
            # visible wherever engine state is reported.
            if self.capability_warnings:
                health["warnings"] = list(self.capability_warnings)
            stealth = self.stealth_status()
            if stealth.get("requested") or stealth.get("active"):
                health["stealth"] = {
                    "active": bool(stealth.get("active")),
                    "tls_impersonation": bool(stealth.get("tls_impersonation")),
                    "captcha_solver": bool(stealth.get("captcha_solver")),
                }
            return health
        from ..proc import probe_command

        probe = probe_command(str(path), ["--version"], timeout=15)
        if probe.ok:
            match = re.search(r"\d+\.\d+(?:\.\d+)?", probe.output or "")
            version = match.group(0) if match else ""
            return {"status": "ok", "message": f"browser engine ready{f' (v{version})' if version else ''}"}
        return {
            "status": "error",
            "message": f"browser engine cannot execute ({probe.status})",
            "hint": "reinstall with: iralens install-engine",
        }
