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
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import errors
from ..security import public_message
from .locate import find_engine

_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def classify_engine_error(text: str) -> errors.IraLensError:
    """Map engine failure text onto the unified error taxonomy."""
    text = str(text)
    while text.startswith("Error: "):
        text = text[len("Error: "):]
    low = text.lower()
    msg = public_message(text)

    if "file://" in low and "disabled" in low:
        return errors.SecurityBlockedError("local file navigation is not allowed")
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
    return errors.IraLensError(msg)


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

    def start(self) -> None:
        if self.running:
            return
        binary = self.binary_path()
        argv = [str(binary), "mcp"]
        if self.config is not None:
            if str(self.config.get("stealth", "")).lower() in ("1", "true", "yes"):
                argv.append("--stealth")
            proxy = self.config.get("proxy")
            if proxy:
                argv += ["--proxy", str(proxy)]
            ua = self.config.get("user_agent")
            if ua:
                argv += ["--user-agent", str(ua)]
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise errors.EngineUnavailableError(
                "could not start the browser engine", detail=public_message(exc)
            ) from exc
        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._reader.start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "iralens", "version": "0.1.0"},
        })
        self._restore_session_state()

    def close(self, *, persist_state: bool = True) -> None:
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
                raise errors.EngineUnavailableError(
                    "lost connection to the browser engine", detail=public_message(exc)
                ) from exc

            deadline_queue: "queue.Queue[dict]" = self._responses
            while True:
                try:
                    payload = deadline_queue.get(timeout=timeout)
                except queue.Empty as exc:
                    raise errors.OperationTimeoutError(
                        f"browser engine did not respond within {timeout}s"
                    ) from exc
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
        args: Dict[str, Any] = {"url": url, "waitUntil": wait_until}
        return self.call("browser_navigate", args, timeout=timeout)

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
            return {"status": "ok", "message": "browser engine running"}
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
