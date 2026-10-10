# -*- coding: utf-8 -*-
"""IraLens command-line interface.

One command, one system:

    iralens search "query"          web search
    iralens open URL                open anything (source-aware)
    iralens read URL                fast static read
    iralens fetch SOURCE OP k=v     specialized source operations
    iralens navigate|click|fill|…   browser interaction
    iralens sources | doctor        capability catalog & health
    iralens session                 unified session state
    iralens mcp                     start the MCP server
    iralens install-engine          install the browser engine

Exit-code contract (D-079):
    0    success, including an honest zero-results answer
    2    caller error (bad input, bad arguments, forbidden target)
    3    internal/operational error
    4    every search engine failed (the query is not answered; see output)
    130  interrupted (Ctrl-C)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from . import __version__
from .core import IraLens
from .errors import IraLensError, SearchEnginesFailedError
from .model import Artifact
from .security import UNTRUSTED_NOTICE, public_message

# Exit codes (D-079). Agents may branch on these; keep them stable.
EXIT_OK = 0
EXIT_CALLER = 2
EXIT_INTERNAL = 3
EXIT_ENGINES_FAILED = 4
EXIT_INTERRUPT = 130


def _jsonable(obj: Any) -> Any:
    """Recursively turn result objects into plain JSON data.

    Objects with a to_dict() method (Artifact, reports, responses) become dicts.
    Without this, json.dumps(default=str) prints Python reprs, which agents
    cannot parse.
    """
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return _jsonable(obj.to_dict())
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _contains_artifact(obj: Any) -> bool:
    if isinstance(obj, Artifact):
        return True
    if isinstance(obj, (list, tuple)):
        return any(_contains_artifact(v) for v in obj)
    return False


def _out(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_jsonable(data), ensure_ascii=False, indent=2, default=str))
        if _contains_artifact(data):
            print(UNTRUSTED_NOTICE, file=sys.stderr)
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
        prog="iralens",
        description="IraLens — one unified Internet-access system: search, read, browse, and specialized sources.",
    )
    parser.add_argument("--json", action="store_true", help="force JSON output")
    # Accept --json after the subcommand too (agents often write it there).
    # SUPPRESS keeps the top-level default when the flag is absent in that position.
    json_flag = argparse.ArgumentParser(add_help=False)
    json_flag.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                           help="force JSON output")
    parser.add_argument("--version", action="version", version=f"iralens {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(parents=[json_flag], name="search", help="search the open web")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--backend", default="", help="semantic-search | browser-search")

    p = sub.add_parser(parents=[json_flag], name="search-api", help="structured search: filters, engine outcomes, fallbacks, ranking")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--engine", action="append", default=[], help="repeatable; e.g. duckduckgo, bing, semantic-search")
    p.add_argument("--date-from", default=None, help="yyyy-mm-dd")
    p.add_argument("--date-to", default=None, help="yyyy-mm-dd")
    p.add_argument("--include-domain", action="append", default=[])
    p.add_argument("--exclude-domain", action="append", default=[])
    p.add_argument("--file-type", default=None, help="e.g. pdf")
    p.add_argument("--language", default=None)
    p.add_argument("--region", default=None, help="e.g. in-en")
    p.add_argument("--no-reformulate", action="store_true")
    p.add_argument("--cache", default="use", choices=["use", "bypass", "refresh"])

    p = sub.add_parser(parents=[json_flag], name="research", help="multi-round research: plan, search, read, evaluate, cite")
    p.add_argument("question")
    p.add_argument("--rounds", type=int, default=None, help="max research rounds")
    p.add_argument("--max-queries", type=int, default=None)
    p.add_argument("--min-sources", type=int, default=None)
    p.add_argument("--read-top", type=int, default=None, help="pages to read per round")

    p = sub.add_parser(parents=[json_flag], name="open", help="open any URL (specialized source when applicable, else browser/reader)")
    p.add_argument("url")
    p.add_argument("--mode", default="auto", choices=["auto", "browser", "static", "source"])
    p.add_argument("--max-chars", type=int, default=20000)

    p = sub.add_parser(parents=[json_flag], name="read", help="fast static read of a URL")
    p.add_argument("url")
    p.add_argument("--mode", default="static", choices=["auto", "static", "browser"])
    p.add_argument("--max-chars", type=int, default=20000,
                   help="content budget in characters (default 20000)")

    p = sub.add_parser(parents=[json_flag], name="scrape", help="bulk-read many URLs (failures don't abort the batch)")
    p.add_argument("urls", nargs="+")
    p.add_argument("--mode", default="static", choices=["auto", "static", "browser"])

    p = sub.add_parser(parents=[json_flag], name="fetch", help="run a specialized source operation")
    p.add_argument("source")
    p.add_argument("op")
    p.add_argument("params", nargs="*", help="key=value pairs")

    sub.add_parser(parents=[json_flag], name="sources", help="list sources and operations")
    sub.add_parser(parents=[json_flag], name="doctor", help="health of every capability")
    sub.add_parser(parents=[json_flag], name="session", help="show unified session state")

    p = sub.add_parser(parents=[json_flag], name="session-reset", help="clear session history/discoveries/browser state")

    p = sub.add_parser(parents=[json_flag], name="configure", help="set a config value (tokens, keys, engine path…)")
    p.add_argument("pairs", nargs="+", help="key=value")

    p = sub.add_parser(parents=[json_flag], name="navigate", help="browser: navigate to URL")
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
        sub.add_parser(parents=[json_flag], name=name, help=help_text)

    p = sub.add_parser(parents=[json_flag], name="click", help="browser: click selector or ref=<id>")
    p.add_argument("selector")
    p = sub.add_parser(parents=[json_flag], name="fill", help="browser: set input value")
    p.add_argument("selector")
    p.add_argument("value")
    p = sub.add_parser(parents=[json_flag], name="type", help="browser: append text")
    p.add_argument("text")
    p.add_argument("--selector", default=None)
    p = sub.add_parser(parents=[json_flag], name="press", help="browser: press a key")
    p.add_argument("key")
    p.add_argument("--selector", default=None)
    p = sub.add_parser(parents=[json_flag], name="select", help="browser: select option value")
    p.add_argument("selector")
    p.add_argument("value")
    p = sub.add_parser(parents=[json_flag], name="scroll", help="browser: scroll")
    p.add_argument("--direction", default="down")
    p.add_argument("--amount", type=int, default=None)
    p = sub.add_parser(parents=[json_flag], name="eval", help="browser: evaluate JavaScript")
    p.add_argument("expression")
    p = sub.add_parser(parents=[json_flag], name="wait-for", help="browser: wait for selector")
    p.add_argument("selector")
    p.add_argument("--timeout", type=int, default=30)
    p = sub.add_parser(parents=[json_flag], name="wait-for-text", help="browser: wait for text")
    p.add_argument("text")
    p.add_argument("--timeout", type=int, default=30)
    p = sub.add_parser(parents=[json_flag], name="find", help="browser: find text in page")
    p.add_argument("query")
    p = sub.add_parser(parents=[json_flag], name="extract", help='browser: extract {"field": "css[@attr]"} JSON')
    p.add_argument("schema_json")
    p = sub.add_parser(parents=[json_flag], name="count", help="browser: count elements")
    p.add_argument("selector")
    p = sub.add_parser(parents=[json_flag], name="attribute", help="browser: read element attribute")
    p.add_argument("selector")
    p.add_argument("name")
    p = sub.add_parser(parents=[json_flag], name="forms-fill", help='browser: fill form {"selector": "value"} JSON')
    p.add_argument("values_json")
    p = sub.add_parser(parents=[json_flag], name="screenshot", help="browser: save PNG screenshot")
    p.add_argument("path")
    p = sub.add_parser(parents=[json_flag], name="pdf", help="browser: save page PDF")
    p.add_argument("path")
    p = sub.add_parser(parents=[json_flag], name="cookies-set", help="browser: set a cookie")
    p.add_argument("name")
    p.add_argument("value")
    p.add_argument("--domain", default="")
    p = sub.add_parser(parents=[json_flag], name="tab-new", help="browser: open new tab")
    p.add_argument("url", nargs="?", default=None)
    p = sub.add_parser(parents=[json_flag], name="tab-switch", help="browser: switch tab")
    p.add_argument("tab_id")
    p = sub.add_parser(parents=[json_flag], name="tab-close", help="browser: close tab")
    p.add_argument("tab_id")
    p = sub.add_parser(parents=[json_flag], name="close", help="close the browser engine (persists session state)")

    sub.add_parser(parents=[json_flag], name="mcp", help="run the IraLens MCP server (stdio)")
    p = sub.add_parser(parents=[json_flag], name="install-engine",
                       help="download and verify the browser engine binary")
    p.add_argument("--status", action="store_true",
                   help="report the current installation state; install nothing")
    p.add_argument("--force", action="store_true",
                   help="reinstall even if a working engine is already installed")
    p.add_argument("--checksum", default=None, metavar="SHA256",
                   help="expected SHA-256 of the archive; aborts on mismatch "
                        "(also: IRALENS_ENGINE_SHA256 environment variable)")
    p.add_argument("--print-url", action="store_true",
                   help="print the exact asset URL for manual download; install nothing")
    sub.add_parser(parents=[json_flag], name="version", help="print version")
    return parser


def run(args: argparse.Namespace) -> tuple:
    """Execute one command; return (result, exit_code) per the D-079 contract."""
    command = args.command

    if command == "version":
        return ({"system": "iralens", "version": __version__}, EXIT_OK)

    if command == "install-engine":
        from .engine.install import ENGINE_VERSION, _asset_name, engine_download_url, engine_status, install_engine

        if getattr(args, "status", False):
            return ({"engine": engine_status()}, EXIT_OK)
        if getattr(args, "print_url", False):
            url = engine_download_url()
            if getattr(args, "json", False):
                return ({"url": url, "asset": _asset_name(), "version": ENGINE_VERSION}, EXIT_OK)
            print(url)
            return (None, EXIT_OK)
        install_report: dict = {}
        path = install_engine(force=getattr(args, "force", False),
                              expected_sha256=getattr(args, "checksum", None),
                              report=install_report)
        return ({"installed": str(path),
                 "already_installed": bool(install_report.get("already_installed")),
                 "checksum_verified": bool(install_report.get("checksum_verified")),
                 "shim": install_report.get("shim")}, EXIT_OK)

    if command == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return (None, EXIT_OK)

    with IraLens() as hil:
        if command == "search":
            return (hil.search(args.query, limit=args.limit, backend=args.backend), EXIT_OK)
        if command == "research":
            opts = {"max_rounds": args.rounds, "max_queries": args.max_queries,
                    "min_sources": args.min_sources, "read_top_n": args.read_top}
            report = hil.research(args.question, options={k: v for k, v in opts.items() if v is not None})
            code = EXIT_ENGINES_FAILED if report.stop_reason == "search_failed" else EXIT_OK
            return (report.to_dict() if getattr(args, "json", False) else report.render_text(), code)
        if command == "search-api":
            response = hil.search_api(
                args.query,
                filters={"date_from": args.date_from, "date_to": args.date_to,
                         "include_domains": args.include_domain, "exclude_domains": args.exclude_domain,
                         "file_type": args.file_type, "language": args.language, "region": args.region},
                options={"max_results": args.limit, "engines": args.engine,
                         "reformulate": not args.no_reformulate, "cache": args.cache},
            )
            code = EXIT_ENGINES_FAILED if response.no_results_reason == "engines_failed" else EXIT_OK
            return (response.to_dict(), code)
        if command == "open":
            return (hil.open(args.url, mode=args.mode, max_chars=args.max_chars), EXIT_OK)
        if command == "read":
            return (hil.read(args.url, mode=args.mode, max_chars=args.max_chars), EXIT_OK)
        if command == "scrape":
            return (hil.scrape(args.urls, mode=args.mode), EXIT_OK)
        if command == "fetch":
            return (hil.fetch(args.source, args.op, **_parse_params(args.params)), EXIT_OK)
        if command == "sources":
            return (hil.sources(), EXIT_OK)
        if command == "doctor":
            return (hil.doctor(), EXIT_OK)
        if command == "session":
            return (hil.session_state(), EXIT_OK)
        if command == "session-reset":
            hil.reset_session()
            return ({"reset": True}, EXIT_OK)
        if command == "configure":
            for key, value in _parse_params(args.pairs).items():
                hil.configure(key, value)
            return ({"configured": list(_parse_params(args.pairs))}, EXIT_OK)
        if command == "navigate":
            return (hil.navigate(args.url, wait_until=args.wait_until), EXIT_OK)
        if command == "back":
            return (hil.back(), EXIT_OK)
        if command == "forward":
            return (hil.forward(), EXIT_OK)
        if command == "reload":
            return (hil.reload(), EXIT_OK)
        if command == "snapshot":
            return (hil.snapshot(), EXIT_OK)
        if command == "markdown":
            return (hil.page_markdown(), EXIT_OK)
        if command == "links":
            return (hil.links(), EXIT_OK)
        if command == "interactive":
            return (hil.interactive_elements(), EXIT_OK)
        if command == "forms-detect":
            return (hil.forms_detect(), EXIT_OK)
        if command == "network-log":
            return (hil.network_log(), EXIT_OK)
        if command == "console-log":
            return (hil.console_log(), EXIT_OK)
        if command == "cookies-get":
            return (hil.cookies_get(), EXIT_OK)
        if command == "cookies-clear":
            return (hil.cookies_clear(), EXIT_OK)
        if command == "storage-state":
            return (hil.storage_state(), EXIT_OK)
        if command == "tab-list":
            return (hil.tab_list(), EXIT_OK)
        if command == "click":
            return (hil.click(args.selector), EXIT_OK)
        if command == "fill":
            return (hil.fill(args.selector, args.value), EXIT_OK)
        if command == "type":
            return (hil.type_text(args.text, args.selector), EXIT_OK)
        if command == "press":
            return (hil.press_key(args.key, args.selector), EXIT_OK)
        if command == "select":
            return (hil.select_option(args.selector, args.value), EXIT_OK)
        if command == "scroll":
            return (hil.scroll(args.direction, args.amount), EXIT_OK)
        if command == "eval":
            return (hil.evaluate_js(args.expression), EXIT_OK)
        if command == "wait-for":
            return (hil.wait_for(args.selector, args.timeout), EXIT_OK)
        if command == "wait-for-text":
            return (hil.wait_for_text(args.text, args.timeout), EXIT_OK)
        if command == "find":
            return (hil.find_in_page(args.query), EXIT_OK)
        if command == "extract":
            return (hil.extract(json.loads(args.schema_json)), EXIT_OK)
        if command == "count":
            return (hil.count(args.selector), EXIT_OK)
        if command == "attribute":
            return (hil.attribute(args.selector, args.name), EXIT_OK)
        if command == "forms-fill":
            return (hil.forms_fill(json.loads(args.values_json)), EXIT_OK)
        if command == "screenshot":
            return (hil.screenshot(args.path), EXIT_OK)
        if command == "pdf":
            return (hil.pdf(args.path), EXIT_OK)
        if command == "cookies-set":
            return (hil.cookies_set(args.name, args.value, args.domain), EXIT_OK)
        if command == "tab-new":
            return (hil.tab_new(args.url), EXIT_OK)
        if command == "tab-switch":
            return (hil.tab_switch(args.tab_id), EXIT_OK)
        if command == "tab-close":
            return (hil.tab_close(args.tab_id), EXIT_OK)
        if command == "close":
            hil.close()
            return ({"closed": True}, EXIT_OK)
    raise SystemExit(f"unknown command: {command}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, code = run(args)
    except SearchEnginesFailedError as exc:
        # Every engine failed (D-079): distinct from caller errors so callers
        # can tell "your input was wrong" apart from "the web did not answer".
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_ENGINES_FAILED
    except IraLensError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_CALLER
    except ValueError as exc:
        # Malformed arguments and rejected filters/queries are caller errors
        # (FilterError is a ValueError), not internal faults — same mapping MCP uses.
        print(json.dumps({"error": "invalid_input", "message": public_message(exc)[:500]},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_CALLER
    except KeyboardInterrupt:
        return EXIT_INTERRUPT
    except Exception as exc:  # never leak raw internal text
        print(
            json.dumps(
                {"error": "internal_error", "message": public_message(exc)[:500]},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return EXIT_INTERNAL
    if result is not None:
        _out(result, getattr(args, "json", False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
