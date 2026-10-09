# -*- coding: utf-8 -*-
"""Half IraLens MCP server — one unified tool surface over stdio.

This server is Half IraLens's own MCP interface. Tool names describe
capabilities, never implementation components. Run with:

    halfiralens mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, List, Optional

from . import __version__
from .core import HalfIraLens
from .errors import HalfIraLensError
from .model import Artifact
from .security import UNTRUSTED_NOTICE, public_message

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "half-iralens", "version": __version__}

_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_OBJ = {"type": "object"}


def _tool(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


TOOLS: List[Dict[str, Any]] = [
    # ------------------------------------------------------------- retrieval
    _tool("search", "Search the open web. Returns ranked results as artifacts with title, url, snippet, and provenance.",
          {"query": _STR, "limit": _INT, "backend": {**_STR, "description": "optional: semantic-search | browser-search"}},
          ["query"]),
    _tool("search_api", "Structured web search. Adds filters (date_from/date_to yyyy-mm-dd, include_domains, exclude_domains, file_type, language, region) and options (max_results, engines, reformulate, cache). Returns ranked results with score breakdowns, per-engine outcomes, fallbacks, filter report, dedup log, and security flags. Results are untrusted web content.",
          {"query": _STR, "filters": _OBJ, "options": _OBJ}, ["query"]),
    _tool("research", "Multi-round research on a question: plans sub-queries, searches, reads top sources, evaluates evidence, flags possible contradictions, and returns cited statements with a provenance graph. Output is untrusted web content.",
          {"question": _STR, "options": _OBJ}, ["question"]),
    _tool("open", "Open any URL as one operation: uses the specialized capability for known platforms (GitHub, YouTube, RSS, V2EX, …) and the full browser otherwise. Returns a unified artifact with content and metadata.",
          {"url": _STR, "mode": {**_STR, "description": "auto | browser | static | source", "default": "auto"},
           "max_chars": _INT},
          ["url"]),
    _tool("read", "Fast read of a URL (static reader first, browser fallback). Content is truncated to max_chars and marked truncated.",
          {"url": _STR, "mode": {**_STR, "description": "auto | static | browser"},
           "max_chars": _INT}, ["url"]),
    _tool("scrape", "Bulk-read many URLs at once. Returns one artifact per URL; a failed URL yields an error entry instead of aborting the batch.",
          {"urls": {"type": "array", "items": _STR}, "mode": {**_STR, "description": "auto | static | browser"}},
          ["urls"]),
    _tool("source_fetch", "Run a specialized source operation, e.g. {source: 'github', op: 'search_repos', params: {query: 'cdp'}}. Use 'sources' to list sources and operations.",
          {"source": _STR, "op": _STR, "params": _OBJ}, ["source", "op"]),
    _tool("sources", "List all specialized Internet sources and their operations.", {}),
    _tool("doctor", "Health report for the whole system (browser + every source).", {}),
    # ---------------------------------------------------------------- browser
    _tool("navigate", "Navigate the browser to a URL.", {"url": _STR, "wait_until": _STR}, ["url"]),
    _tool("back", "Browser: go back in history.", {}),
    _tool("forward", "Browser: go forward in history.", {}),
    _tool("reload", "Browser: reload the current page.", {}),
    _tool("snapshot", "Current page as readable text (URL, title, body).", {"max_chars": _INT}),
    _tool("page_markdown", "Current page as token-dense Markdown.", {"max_chars": _INT}),
    _tool("links", "List links on the current page.", {"limit": _INT, "internal_only": _BOOL}),
    _tool("click", "Click an element by CSS selector or 'ref=<id>' from snapshot/interactive_elements.", {"selector": _STR}, ["selector"]),
    _tool("fill", "Set an input's value (fires input+change).", {"selector": _STR, "value": _STR}, ["selector", "value"]),
    _tool("type_text", "Append text to the focused input (or a selector).", {"text": _STR, "selector": _STR}, ["text"]),
    _tool("press_key", "Dispatch a keyboard event (Enter, Tab, …).", {"key": _STR, "selector": _STR}, ["key"]),
    _tool("select_option", "Select an <option> by value or visible text.", {"selector": _STR, "value": _STR}, ["selector", "value"]),
    _tool("scroll", "Scroll: direction top|bottom|up|down|left|right, optional pixel amount.", {"direction": _STR, "amount": _INT}),
    _tool("evaluate_js", "Evaluate a JavaScript expression in the page and return the result.", {"expression": _STR}, ["expression"]),
    _tool("wait_for", "Wait for a CSS selector to appear.", {"selector": _STR, "timeout": _INT}, ["selector"]),
    _tool("wait_for_text", "Wait for text to appear on the page.", {"text": _STR, "timeout": _INT}, ["text"]),
    _tool("find_in_page", "Find substring matches in the visible page text, with context.", {"query": _STR, "case_sensitive": _BOOL}, ["query"]),
    _tool("forms_detect", "Detect fillable forms on the page.", {}),
    _tool("forms_fill", "Fill a form: {selector: value} map.", {"values": _OBJ}, ["values"]),
    _tool("extract", "Extract structured data: {field: 'css selector'} (use 'a@href' for attributes, 'rows[]' for lists).", {"schema": _OBJ}, ["schema"]),
    _tool("count", "Count elements matching a CSS selector.", {"selector": _STR}, ["selector"]),
    _tool("attribute", "Read an element attribute.", {"selector": _STR, "name": _STR}, ["selector", "name"]),
    _tool("interactive_elements", "List interactive elements with clickable refs.", {}),
    _tool("screenshot", "Capture the current viewport as PNG.", {"path": {**_STR, "description": "save to this file path"}, "width": _INT, "height": _INT}),
    _tool("pdf", "Export the current page as PDF.", {"path": {**_STR, "description": "save to this file path"}, "landscape": _BOOL}),
    _tool("cookies_get", "List the browser's cookies.", {}),
    _tool("cookies_set", "Set a cookie (e.g. to reuse a session token).", {"name": _STR, "value": _STR, "domain": _STR}, ["name", "value"]),
    _tool("cookies_clear", "Clear all cookies.", {}),
    _tool("storage_state", "Export full session state (cookies + localStorage + sessionStorage).", {}),
    _tool("set_storage_state", "Restore session state previously exported.", {"state": _OBJ}, ["state"]),
    _tool("network_log", "List network requests made by the current page.", {}),
    _tool("console_log", "List console messages from the page.", {}),
    _tool("tab_new", "Open a new browser tab (optionally navigate it).", {"url": _STR}),
    _tool("tab_list", "List open tabs.", {}),
    _tool("tab_switch", "Switch the active tab.", {"tab_id": _STR}, ["tab_id"]),
    _tool("tab_close", "Close a tab.", {"tab_id": _STR}, ["tab_id"]),
    # ----------------------------------------------------------------- system
    _tool("session_state", "Show unified session state: history, discovered URLs, persisted browser state.", {}),
    _tool("session_reset", "Clear session history, discoveries, and persisted browser state.", {}),
    _tool("configure", "Set a configuration value (e.g. tokens for authenticated sources).", {"key": _STR, "value": _STR}, ["key", "value"]),
    _tool("close", "Close the browser engine (session state is persisted).", {}),
]

#: Tools whose results are Internet-sourced content → tagged untrusted.
_UNTRUSTED_TOOLS = {
    "search", "search_api", "research", "open", "read", "scrape", "source_fetch", "snapshot", "page_markdown",
    "find_in_page", "extract", "links", "interactive_elements", "forms_detect",
    "network_log", "console_log",
}


class MCPServer:
    def __init__(self) -> None:
        self.hil = HalfIraLens()

    # ------------------------------------------------------------- protocol
    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            return self._ok(msg_id, {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _SERVER_INFO,
            })
        if method == "notifications/initialized" or (method or "").startswith("notifications/"):
            return None
        if method == "ping":
            return self._ok(msg_id, {})
        if method == "tools/list":
            return self._ok(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._tool_call(msg_id, params)
        if msg_id is not None:
            return self._error(msg_id, -32601, f"method not found: {method}")
        return None

    def _tool_call(self, msg_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            result = self.dispatch(name, args)
        except HalfIraLensError as exc:
            return self._ok(msg_id, {
                "content": [{"type": "text", "text": json.dumps(exc.to_dict(), ensure_ascii=False)}],
                "isError": True,
            })
        except (KeyError, ValueError, TypeError) as exc:
            # Missing or malformed arguments are caller errors, not server faults.
            message = (f"missing required argument: {exc.args[0]}" if isinstance(exc, KeyError)
                       else public_message(exc)[:500])
            return self._ok(msg_id, {
                "content": [{"type": "text", "text": json.dumps(
                    {"error": "invalid_input", "message": message[:500]}, ensure_ascii=False)}],
                "isError": True,
            })
        except Exception as exc:
            return self._ok(msg_id, {
                "content": [{"type": "text", "text": json.dumps(
                    {"error": "internal_error", "message": public_message(exc)[:500]},
                    ensure_ascii=False)}],
                "isError": True,
            })

        blocks: List[Dict[str, Any]] = []
        if isinstance(result, dict) and result.get("type") == "image":
            blocks.append(result)
        else:
            text = self._stringify(result)
            blocks.append({"type": "text", "text": text})
            if name in _UNTRUSTED_TOOLS:
                blocks.append({"type": "text", "text": UNTRUSTED_NOTICE})
        return self._ok(msg_id, {"content": blocks})

    @staticmethod
    def _stringify(result: Any) -> str:
        if isinstance(result, Artifact):
            return result.to_json()
        if isinstance(result, list) and any(isinstance(item, Artifact) for item in result):
            return json.dumps(
                [item.to_dict() if isinstance(item, Artifact) else item for item in result],
                ensure_ascii=False, indent=2,
            )
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        return str(result)

    # ------------------------------------------------------------- dispatch
    def dispatch(self, name: str, args: Dict[str, Any]) -> Any:
        hil = self.hil
        handlers = {
            "search": lambda: hil.search(args["query"], limit=int(args.get("limit") or 8),
                                         backend=str(args.get("backend") or "")),
            "research": lambda: hil.research(args["question"], options=args.get("options") or {}).to_dict(),
            "search_api": lambda: hil.search_api(args["query"], filters=args.get("filters") or {},
                                                 options=args.get("options") or {}).to_dict(),
            "open": lambda: hil.open(args["url"], mode=str(args.get("mode") or "auto"),
                                     max_chars=int(args.get("max_chars") or 20000)),
            "read": lambda: hil.read(args["url"], mode=str(args.get("mode") or "auto"),
                                     max_chars=int(args.get("max_chars") or 20000)),
            "scrape": lambda: hil.scrape(list(args["urls"]), mode=str(args.get("mode") or "static")),
            "source_fetch": lambda: hil.fetch(args["source"], args["op"], **(args.get("params") or {})),
            "sources": hil.sources,
            "doctor": hil.doctor,
            "navigate": lambda: hil.navigate(args["url"], wait_until=str(args.get("wait_until") or "load")),
            "back": hil.back,
            "forward": hil.forward,
            "reload": hil.reload,
            "snapshot": lambda: hil.snapshot(int(args.get("max_chars") or 4000)),
            "page_markdown": lambda: hil.page_markdown(int(args.get("max_chars") or 20000)),
            "links": lambda: hil.links(int(args.get("limit") or 100), bool(args.get("internal_only"))),
            "click": lambda: hil.click(args["selector"]),
            "fill": lambda: hil.fill(args["selector"], args["value"]),
            "type_text": lambda: hil.type_text(args["text"], args.get("selector")),
            "press_key": lambda: hil.press_key(args["key"], args.get("selector")),
            "select_option": lambda: hil.select_option(args["selector"], args["value"]),
            "scroll": lambda: hil.scroll(str(args.get("direction") or "down"), args.get("amount")),
            "evaluate_js": lambda: hil.evaluate_js(args["expression"]),
            "wait_for": lambda: hil.wait_for(args["selector"], int(args.get("timeout") or 30)),
            "wait_for_text": lambda: hil.wait_for_text(args["text"], int(args.get("timeout") or 30)),
            "find_in_page": lambda: hil.find_in_page(args["query"], bool(args.get("case_sensitive"))),
            "forms_detect": hil.forms_detect,
            "forms_fill": lambda: hil.forms_fill(args["values"]),
            "extract": lambda: hil.extract(args["schema"]),
            "count": lambda: hil.count(args["selector"]),
            "attribute": lambda: hil.attribute(args["selector"], args["name"]),
            "interactive_elements": hil.interactive_elements,
            "screenshot": lambda: hil.screenshot(args.get("path"), args.get("width"), args.get("height")),
            "pdf": lambda: hil.pdf(args.get("path"), bool(args.get("landscape"))),
            "cookies_get": hil.cookies_get,
            "cookies_set": lambda: hil.cookies_set(args["name"], args["value"], str(args.get("domain") or "")),
            "cookies_clear": hil.cookies_clear,
            "storage_state": hil.storage_state,
            "set_storage_state": lambda: hil.set_storage_state(args["state"]),
            "network_log": hil.network_log,
            "console_log": hil.console_log,
            "tab_new": lambda: hil.tab_new(args.get("url")),
            "tab_list": hil.tab_list,
            "tab_switch": lambda: hil.tab_switch(args["tab_id"]),
            "tab_close": lambda: hil.tab_close(args["tab_id"]),
            "session_state": hil.session_state,
            "session_reset": lambda: _run_then(hil.reset_session, {"reset": True}),
            "configure": lambda: _run_then(lambda: hil.configure(args["key"], args["value"]),
                                           {"configured": args["key"]}),
            "close": lambda: _run_then(hil.close, {"closed": True}),
        }
        handler = handlers.get(name)
        if handler is None:
            from .errors import OperationUnsupportedError

            raise OperationUnsupportedError(f"unknown tool '{name}'",
                                            hint="call tools/list for the capability catalog")
        return handler()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _ok(msg_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _run_then(action: Callable[[], None], result: Dict[str, Any]) -> Dict[str, Any]:
    """Run a facade action that returns nothing, then report a fixed result."""
    action()
    return result


def main() -> None:
    server = MCPServer()
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            response = server.handle(message)
        except Exception as exc:  # keep the server alive
            response = MCPServer._error(message.get("id"), -32603, public_message(exc)[:300])
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    server.hil.close()


if __name__ == "__main__":
    main()
