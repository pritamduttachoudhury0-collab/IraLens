# -*- coding: utf-8 -*-
"""Integrated MCP JSON-RPC 2.0 Server & CLI entrypoint for IraLens Browser Engine.

Implements the MCP stdio protocol (`initialize`, `notifications/initialized`,
`tools/list`, `tools/call`, `ping`) and CLI subcommands (`mcp`, `fetch`, `scrape`,
`--version`) backed by `ObscuraNativeEngine`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...errors import (
    EngineUnavailableError,
    ExtractionError,
    IraLensError,
    OperationTimeoutError,
    PageUnavailableError,
    SecurityBlockedError,
)
from .runtime import (
    UPSTREAM_BASE_TAG,
    UPSTREAM_REVISION,
    ObscuraNativeEngine,
)

#: Channel used to hand the proxy to a spawned engine without putting it on
#: the command line. `/proc/<pid>/cmdline` is world-readable, so a proxy URL
#: carrying credentials (`http://user:pass@host`) would be readable by every
#: local account. The environment is owner-readable only, which is weaker
#: exposure but still not zero, so keep it out of logs either way.
PROXY_ENV_VAR = "IRALENS_ENGINE_PROXY"

ENGINE_SERVER_NAME = "iralens-browser-engine"
ENGINE_SERVER_VERSION = f"0.2.4+rev.{UPSTREAM_REVISION[:12]}"

#: What this build actually implements, as opposed to what the vendored
#: upstream source is capable of. The distinction matters: the vendored
#: Obscura crates carry real stealth code behind Rust `#[cfg(feature =
#: "stealth")]` gates, and this distribution does not compile the Rust
#: crates at all -- it runs the Python/native-V8 tier. The capability is
#: therefore *upstream*, not *present*. Advertising it as available would
#: let a caller believe requests are protected when they are not.
#:
#: Even where stealth is compiled in, it is a browser-identity consistency
#: measure. It does not solve CAPTCHAs and does not guarantee success
#: against interactive Cloudflare / DataDome / Akamai challenges.
ENGINE_CAPABILITIES: Dict[str, Any] = {
    "stealth": {
        "supported": False,
        "tier": "python-native-v8",
        "reason": (
            "stealth is implemented in the vendored Rust crate "
            "obscura-browser behind the 'stealth' cargo feature, which this "
            "distribution does not compile; the Python/native-V8 tier has no "
            "stealth implementation"
        ),
    },
    "javascript_rendering": {"supported": True},
    "proxy": {"supported": True},
    "tracker_blocking": {"supported": False},
}


def _stealth_notice() -> str:
    """The diagnostic shown when `--stealth` is requested but unsupported."""
    info = ENGINE_CAPABILITIES["stealth"]
    return (
        f"{ENGINE_SERVER_NAME}: --stealth was requested but is not supported by "
        f"this build ({info['tier']}); continuing without it. {info['reason']}. "
        "Note that stealth, where available, does not solve CAPTCHAs and does "
        "not guarantee success against interactive challenges."
    )


def capabilities_payload(stealth_requested: bool = False) -> Dict[str, Any]:
    """Capabilities for the `initialize` response.

    A requested-but-unsupported feature is reported explicitly rather than
    being dropped, so the client can tell the difference between "off
    because you did not ask" and "off because this build cannot".
    """
    payload = {name: dict(value) for name, value in ENGINE_CAPABILITIES.items()}
    payload["stealth"]["requested"] = bool(stealth_requested)
    payload["stealth"]["applied"] = False
    return payload


MCP_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": "browser_navigate",
        "description": "Navigate the active browser tab to a URL, execute page scripts, and return navigation metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "none"]},
                "timeout": {"type": "number"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": "Capture an accessibility tree snapshot of the current page with interactive [ref=N] markers.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_markdown",
        "description": "Convert the rendered DOM of the current page into clean Markdown.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_links",
        "description": "Extract all resolved hyperlinks from the rendered DOM.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_interactive_elements",
        "description": "List interactive elements on the page with their ref IDs and selectors.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_click",
        "description": "Click an element by interactive ref ID or CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "integer"},
                "selector": {"type": "string"},
            },
        },
    },
    {
        "name": "browser_fill",
        "description": "Clear and fill a form input or textarea by ref ID or CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "ref": {"type": "integer"},
                "selector": {"type": "string"},
            },
            "required": ["value"],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text character-by-character into an element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "ref": {"type": "integer"},
                "selector": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_press_key",
        "description": "Dispatch keyboard events for a named key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "ref": {"type": "integer"},
                "selector": {"type": "string"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "browser_select_option",
        "description": "Select an option in a <select> element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "ref": {"type": "integer"},
                "selector": {"type": "string"},
            },
            "required": ["value"],
        },
    },
    {
        "name": "browser_detect_forms",
        "description": "Inspect all forms and their input fields on the page.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_fill_form",
        "description": "Batch-fill multiple form fields and optionally submit the form.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fields": {"type": "object"},
                "submit": {"type": "boolean"},
            },
            "required": ["fields"],
        },
    },
    {
        "name": "browser_extract",
        "description": "Extract structured data from elements matching a CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_get_attribute",
        "description": "Read an attribute value from an element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "attribute": {"type": "string"},
                "selector": {"type": "string"},
                "ref": {"type": "integer"},
            },
            "required": ["attribute"],
        },
    },
    {
        "name": "browser_count",
        "description": "Count elements matching a CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
            "required": ["selector"],
        },
    },
    {
        "name": "browser_search",
        "description": "Search visible text on the page and return contextual snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "context_chars": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browser_evaluate",
        "description": "Evaluate a JavaScript expression in the page V8 isolate.",
        "inputSchema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "name": "browser_wait_for",
        "description": "Wait for a CSS selector or duration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "timeout": {"type": "integer"},
            },
        },
    },
    {
        "name": "browser_wait_for_text",
        "description": "Wait until text appears in the page DOM.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_wait_for_function",
        "description": "Wait until a JavaScript expression evaluates truthy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the viewport or scroll an element into view.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string"},
                "amount": {"type": "integer"},
                "selector": {"type": "string"},
            },
        },
    },
    {
        "name": "browser_back",
        "description": "Navigate back in tab session history.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_forward",
        "description": "Navigate forward in tab session history.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_reload",
        "description": "Reload the current page.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_get_cookies",
        "description": "Return cookies in the browser cookie jar.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
        },
    },
    {
        "name": "browser_set_cookie",
        "description": "Set a cookie in the browser cookie jar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "string"},
                "domain": {"type": "string"},
                "path": {"type": "string"},
                "secure": {"type": "boolean"},
                "http_only": {"type": "boolean"},
                "same_site": {"type": "string"},
                "expires": {"type": "integer"},
            },
            "required": ["name", "value"],
        },
    },
    {
        "name": "browser_clear_cookies",
        "description": "Clear all cookies from the browser cookie jar.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_storage_state",
        "description": "Export cookies and per-origin localStorage.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_set_storage_state",
        "description": "Restore cookies and per-origin localStorage.",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": ["string", "object"]}},
            "required": ["state"],
        },
    },
    {
        "name": "browser_tab_new",
        "description": "Open a new browser tab and optionally navigate to a URL.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
        },
    },
    {
        "name": "browser_tab_list",
        "description": "List all open browser tabs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_tab_switch",
        "description": "Switch the active browser tab.",
        "inputSchema": {
            "type": "object",
            "properties": {"tab_id": {"type": "string"}},
            "required": ["tab_id"],
        },
    },
    {
        "name": "browser_tab_close",
        "description": "Close a browser tab by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"tab_id": {"type": "string"}},
        },
    },
    {
        "name": "browser_network_requests",
        "description": "Return recorded HTTP requests for the active tab.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_console_messages",
        "description": "Return captured console log/warn/error messages for the active tab.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_close",
        "description": "Close the browser engine and persist storage state.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _format_error_for_mcp(exc: BaseException) -> str:
    if isinstance(exc, IraLensError):
        msg = f"{exc.message} ({exc.detail})" if exc.detail else exc.message
        if isinstance(exc, SecurityBlockedError):
            return f"SSRFBlocked: {msg}"
        if isinstance(exc, OperationTimeoutError):
            return f"NavigationTimeout: {msg}"
        if isinstance(exc, PageUnavailableError):
            return f"NetworkError: {msg}"
        if isinstance(exc, ExtractionError):
            return f"SelectorError: {msg}"
        if isinstance(exc, EngineUnavailableError):
            return f"TabError: {msg}"
        return f"Error: {msg}"
    return f"Error: {exc}"


def _serialize_tool_output(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return str(val)


def run_mcp_stdio_loop(engine: ObscuraNativeEngine,
                       *, stealth_requested: bool = False) -> int:
    """Run the JSON-RPC 2.0 MCP server loop over stdin/stdout."""
    try:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
                continue

            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params") or {}

            # JSON-RPC notifications have no "id"
            if req_id is None and method.startswith("notifications/"):
                continue

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": ENGINE_SERVER_NAME,
                            "version": ENGINE_SERVER_VERSION,
                        },
                        "engineCapabilities": capabilities_payload(stealth_requested),
                    },
                }
            elif method == "ping":
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": MCP_TOOLS_SCHEMA},
                }
            elif method == "tools/call":
                tool_name = str(params.get("name") or "")
                tool_args = params.get("arguments") or {}
                try:
                    out = engine.dispatch_tool(tool_name, tool_args)
                    text = _serialize_tool_output(out)
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": text}],
                            "isError": False,
                        },
                    }
                except Exception as exc:
                    err_text = _format_error_for_mcp(exc)
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": err_text}],
                            "isError": True,
                        },
                    }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        engine.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iralens-browser-engine",
        description="IraLens integrated native browser engine (V8 + HTML5 DOM + MCP server).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print engine version and vendored revision and exit.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    p_mcp = sub.add_parser("mcp", help="Run the JSON-RPC 2.0 MCP server over stdio.")
    p_mcp.add_argument("--proxy", default=None, help="HTTP/HTTPS proxy URL.")
    p_mcp.add_argument("--user-agent", default=None, help="Custom User-Agent header.")
    p_mcp.add_argument("--storage-dir", default=None, help="Directory for cookie/storage persistence.")
    p_mcp.add_argument("--stealth", action="store_true", help="Enable stealth navigator profile.")
    p_mcp.add_argument(
        "--allow-private",
        action="store_true",
        help="Allow navigation to private/loopback IP addresses.",
    )
    p_mcp.add_argument(
        "--allow-file",
        action="store_true",
        help="Allow navigation to file:// URLs.",
    )

    p_fetch = sub.add_parser("fetch", help="Navigate to a URL and output rendered content.")
    p_fetch.add_argument("url", help="Target URL.")
    p_fetch.add_argument(
        "--format",
        choices=("markdown", "html", "text", "json", "links"),
        default="markdown",
        help="Output format.",
    )
    p_fetch.add_argument("--timeout", type=float, default=30.0, help="Navigation timeout in seconds.")
    p_fetch.add_argument("--allow-private", action="store_true", help="Allow loopback/private IPs.")

    p_scrape = sub.add_parser("scrape", help="Extract elements matching a CSS selector as JSON.")
    p_scrape.add_argument("url", help="Target URL.")
    p_scrape.add_argument("--selector", required=True, help="CSS selector to extract.")
    p_scrape.add_argument("--timeout", type=float, default=30.0, help="Navigation timeout in seconds.")
    p_scrape.add_argument("--allow-private", action="store_true", help="Allow loopback/private IPs.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(
            f"{ENGINE_SERVER_NAME} {ENGINE_SERVER_VERSION} "
            f"(base {UPSTREAM_BASE_TAG}, rev {UPSTREAM_REVISION[:12]})"
        )
        return 0

    stealth_requested = bool(getattr(args, "stealth", False))
    if stealth_requested:
        # Loud, not fatal: a caller who believes stealth is on must be told it
        # is not, but refusing to start would take the whole browser tier down
        # over an optional cosmetic flag.
        print(_stealth_notice(), file=sys.stderr)

    if args.subcommand in (None, "mcp"):
        # Explicit --proxy wins; env is the safe channel used by the parent.
        if getattr(args, "proxy", None) is None:
            env_proxy = os.environ.get(PROXY_ENV_VAR)
            if env_proxy:
                args.proxy = env_proxy
        raw_storage = getattr(args, "storage_dir", None)
        storage_dir = Path(str(raw_storage)).expanduser() if raw_storage else None
        engine = ObscuraNativeEngine(
            user_agent=getattr(args, "user_agent", None),
            proxy_url=getattr(args, "proxy", None),
            storage_dir=storage_dir,
            allow_private=bool(getattr(args, "allow_private", False)),
            allow_file=bool(getattr(args, "allow_file", False)),
        )
        return run_mcp_stdio_loop(engine, stealth_requested=stealth_requested)

    if args.subcommand == "fetch":
        engine = ObscuraNativeEngine(allow_private=bool(args.allow_private))
        try:
            outcome = engine.navigate(args.url, timeout=args.timeout)
            if args.format == "markdown":
                print(engine.dispatch_tool("browser_markdown", {}))
            elif args.format == "links":
                print(json.dumps(engine.dispatch_tool("browser_links", {}), indent=2))
            elif args.format == "html":
                print(engine.dispatch_tool("browser_evaluate", {"expression": "document.documentElement.outerHTML"}))
            elif args.format == "text":
                print(engine.dispatch_tool("browser_evaluate", {"expression": "document.body.innerText"}))
            else:
                md = engine.dispatch_tool("browser_markdown", {})
                payload = outcome.to_dict()
                payload["markdown"] = md
                print(json.dumps(payload, indent=2))
            return 0 if outcome.ok else 1
        finally:
            engine.close()

    if args.subcommand == "scrape":
        engine = ObscuraNativeEngine(allow_private=bool(args.allow_private))
        try:
            outcome = engine.navigate(args.url, timeout=args.timeout)
            items = engine.dispatch_tool("browser_extract", {"selector": args.selector})
            print(json.dumps(items, indent=2))
            return 0 if outcome.ok else 1
        finally:
            engine.close()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
