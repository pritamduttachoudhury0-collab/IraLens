# -*- coding: utf-8 -*-
"""Half IraLens command-line interface.

One command, one system:

    halfiralens search "query"          web search
    halfiralens open URL                open anything (source-aware)
    halfiralens read URL                fast static read
    halfiralens fetch SOURCE OP k=v     specialized source operations
    halfiralens navigate|click|fill|…   browser interaction
    halfiralens sources | doctor        capability catalog & health
    halfiralens session                 unified session state
    halfiralens mcp                     start the MCP server
    halfiralens install-engine          install the browser engine
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from . import __version__
from .core import HalfIraLens
from .errors import HalfIraLensError
from .model import Artifact
from .security import UNTRUSTED_NOTICE, public_message


def _out(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif isinstance(data, Artifact):
        print(data.to_json())
        print(UNTRUSTED_NOTICE, file=sys.stderr)
    elif isinstance(data, list) and data and any(isinstance(item, Artifact) for item in data):
        print(json.dumps(
            [item.to_dict() if isinstance(item, Artifact) else item for item in data],
            ensure_ascii=False, indent=2,
        ))
        print(UNTRUSTED_NOTICE, file=sys.stderr)
    elif isinstance(data, (dict, list)):
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(data)


def _parse_params(pairs: List[str]) -> dict:
    params: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"parameter must be key=value, got: {pair}")
        key, value = pair.split("=", 1)
        params[key.strip()] = value
    return params


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="halfiralens",
        description="Half IraLens — one unified Internet-access system: search, read, browse, and specialized sources.",
    )
    parser.add_argument("--json", action="store_true", help="force JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="search the open web")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--backend", default="", help="semantic-search | browser-search")

    p = sub.add_parser("open", help="open any URL (specialized source when applicable, else browser/reader)")
    p.add_argument("url")
    p.add_argument("--mode", default="auto", choices=["auto", "browser", "static", "source"])
    p.add_argument("--max-chars", type=int, default=20000)

    p = sub.add_parser("read", help="fast static read of a URL")
    p.add_argument("url")
    p.add_argument("--mode", default="static", choices=["auto", "static", "browser"])

    p = sub.add_parser("scrape", help="bulk-read many URLs (failures don't abort the batch)")
    p.add_argument("urls", nargs="+")
    p.add_argument("--mode", default="static", choices=["auto", "static", "browser"])

    p = sub.add_parser("fetch", help="run a specialized source operation")
    p.add_argument("source")
    p.add_argument("op")
    p.add_argument("params", nargs="*", help="key=value pairs")

    sub.add_parser("sources", help="list sources and operations")
    sub.add_parser("doctor", help="health of every capability")
    sub.add_parser("session", help="show unified session state")

    p = sub.add_parser("session-reset", help="clear session history/discoveries/browser state")

    p = sub.add_parser("configure", help="set a config value (tokens, keys, engine path…)")
    p.add_argument("pairs", nargs="+", help="key=value")

    p = sub.add_parser("navigate", help="browser: navigate to URL")
    p.add_argument("url")
    p.add_argument("--wait-until", default="load")

    for name, help_text in (
        ("back", "browser: go back"),
        ("forward", "browser: go forward"),
        ("reload", "browser: reload"),
        ("snapshot", "browser: readable page text"),
        ("markdown", "browser: page as markdown"),
        ("links", "browser: list page links"),
        ("interactive", "browser: list interactive elements"),
        ("forms-detect", "browser: detect forms"),
        ("network-log", "browser: network requests"),
        ("console-log", "browser: console messages"),
        ("cookies-get", "browser: get cookies"),
        ("cookies-clear", "browser: clear cookies"),
        ("storage-state", "browser: export session storage state"),
        ("tab-list", "browser: list tabs"),
    ):
        sub.add_parser(name, help=help_text)

    p = sub.add_parser("click", help="browser: click selector or ref=<id>")
    p.add_argument("selector")
    p = sub.add_parser("fill", help="browser: set input value")
    p.add_argument("selector")
    p.add_argument("value")
    p = sub.add_parser("type", help="browser: append text")
    p.add_argument("text")
    p.add_argument("--selector", default=None)
    p = sub.add_parser("press", help="browser: press a key")
    p.add_argument("key")
    p.add_argument("--selector", default=None)
    p = sub.add_parser("select", help="browser: select option value")
    p.add_argument("selector")
    p.add_argument("value")
    p = sub.add_parser("scroll", help="browser: scroll")
    p.add_argument("--direction", default="down")
    p.add_argument("--amount", type=int, default=None)
    p = sub.add_parser("eval", help="browser: evaluate JavaScript")
    p.add_argument("expression")
    p = sub.add_parser("wait-for", help="browser: wait for selector")
    p.add_argument("selector")
    p.add_argument("--timeout", type=int, default=30)
    p = sub.add_parser("wait-for-text", help="browser: wait for text")
    p.add_argument("text")
    p.add_argument("--timeout", type=int, default=30)
    p = sub.add_parser("find", help="browser: find text in page")
    p.add_argument("query")
    p = sub.add_parser("extract", help='browser: extract {"field": "css[@attr]"} JSON')
    p.add_argument("schema_json")
    p = sub.add_parser("count", help="browser: count elements")
    p.add_argument("selector")
    p = sub.add_parser("attribute", help="browser: read element attribute")
    p.add_argument("selector")
    p.add_argument("name")
    p = sub.add_parser("forms-fill", help='browser: fill form {"selector": "value"} JSON')
    p.add_argument("values_json")
    p = sub.add_parser("screenshot", help="browser: save PNG screenshot")
    p.add_argument("path")
    p = sub.add_parser("pdf", help="browser: save page PDF")
    p.add_argument("path")
    p = sub.add_parser("cookies-set", help="browser: set a cookie")
    p.add_argument("name")
    p.add_argument("value")
    p.add_argument("--domain", default="")
    p = sub.add_parser("tab-new", help="browser: open new tab")
    p.add_argument("url", nargs="?", default=None)
    p = sub.add_parser("tab-switch", help="browser: switch tab")
    p.add_argument("tab_id")
    p = sub.add_parser("tab-close", help="browser: close tab")
    p.add_argument("tab_id")
    p = sub.add_parser("close", help="close the browser engine (persists session state)")

    sub.add_parser("mcp", help="run the Half IraLens MCP server (stdio)")
    sub.add_parser("install-engine", help="download the browser engine binary")
    sub.add_parser("version", help="print version")
    return parser


def run(args: argparse.Namespace) -> Any:
    command = args.command

    if command == "version":
        return {"system": "half-iralens", "version": __version__}

    if command == "install-engine":
        from .engine.install import install_engine

        path = install_engine()
        return {"installed": str(path)}

    if command == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return None

    with HalfIraLens() as hil:
        if command == "search":
            return hil.search(args.query, limit=args.limit, backend=args.backend)
        if command == "open":
            return hil.open(args.url, mode=args.mode, max_chars=args.max_chars)
        if command == "read":
            return hil.read(args.url, mode=args.mode)
        if command == "scrape":
            return hil.scrape(args.urls, mode=args.mode)
        if command == "fetch":
            return hil.fetch(args.source, args.op, **_parse_params(args.params))
        if command == "sources":
            return hil.sources()
        if command == "doctor":
            return hil.doctor()
        if command == "session":
            return hil.session_state()
        if command == "session-reset":
            hil.reset_session()
            return {"reset": True}
        if command == "configure":
            for key, value in _parse_params(args.pairs).items():
                hil.configure(key, value)
            return {"configured": list(_parse_params(args.pairs))}
        if command == "navigate":
            return hil.navigate(args.url, wait_until=args.wait_until)
        if command == "back":
            return hil.back()
        if command == "forward":
            return hil.forward()
        if command == "reload":
            return hil.reload()
        if command == "snapshot":
            return hil.snapshot()
        if command == "markdown":
            return hil.page_markdown()
        if command == "links":
            return hil.links()
        if command == "interactive":
            return hil.interactive_elements()
        if command == "forms-detect":
            return hil.forms_detect()
        if command == "network-log":
            return hil.network_log()
        if command == "console-log":
            return hil.console_log()
        if command == "cookies-get":
            return hil.cookies_get()
        if command == "cookies-clear":
            return hil.cookies_clear()
        if command == "storage-state":
            return hil.storage_state()
        if command == "tab-list":
            return hil.tab_list()
        if command == "click":
            return hil.click(args.selector)
        if command == "fill":
            return hil.fill(args.selector, args.value)
        if command == "type":
            return hil.type_text(args.text, args.selector)
        if command == "press":
            return hil.press_key(args.key, args.selector)
        if command == "select":
            return hil.select_option(args.selector, args.value)
        if command == "scroll":
            return hil.scroll(args.direction, args.amount)
        if command == "eval":
            return hil.evaluate_js(args.expression)
        if command == "wait-for":
            return hil.wait_for(args.selector, args.timeout)
        if command == "wait-for-text":
            return hil.wait_for_text(args.text, args.timeout)
        if command == "find":
            return hil.find_in_page(args.query)
        if command == "extract":
            return hil.extract(json.loads(args.schema_json))
        if command == "count":
            return hil.count(args.selector)
        if command == "attribute":
            return hil.attribute(args.selector, args.name)
        if command == "forms-fill":
            return hil.forms_fill(json.loads(args.values_json))
        if command == "screenshot":
            return hil.screenshot(args.path)
        if command == "pdf":
            return hil.pdf(args.path)
        if command == "cookies-set":
            return hil.cookies_set(args.name, args.value, args.domain)
        if command == "tab-new":
            return hil.tab_new(args.url)
        if command == "tab-switch":
            return hil.tab_switch(args.tab_id)
        if command == "tab-close":
            return hil.tab_close(args.tab_id)
        if command == "close":
            hil.close()
            return {"closed": True}
    raise SystemExit(f"unknown command: {command}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except HalfIraLensError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # never leak raw internal text
        print(
            json.dumps(
                {"error": "internal_error", "message": public_message(exc)[:500]},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 3
    if result is not None:
        _out(result, getattr(args, "json", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
