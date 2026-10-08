# -*- coding: utf-8 -*-
"""XiaoHongShu (小红书) — notes, search, comments.

Backends: desktop browser bridge first (user's own session), then the
xiaohongshu MCP server via the MCP bridge CLI (manual cookie export only —
Half IraLens never logs in for the user and never reads browser cookies).

Platform constraint preserved from the study: xsec_token is mandatory —
notes must be read with the full URL/ID taken from search/feed results.
"""

from __future__ import annotations

import json
from typing import Any, Dict, TYPE_CHECKING

from ..errors import SourceUnavailableError
from ..model import Artifact
from ..proc import run_cli, run_cli_json
from ..security import host_matches
from shutil import which as shutil_which
from ._opencli import opencli_status
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context


def _mcporter_xhs_ready() -> bool:
    if not shutil_which("mcporter"):
        return False
    from ._mcporter import mcporter_server_names

    return "xiaohongshu" in mcporter_server_names()


class XiaoHongShuSource(Source):
    name = "xiaohongshu"
    description = "XiaoHongShu notes, search, comments"
    backends = ["browser-bridge", "xhs-mcp"]
    tier = 2
    operations = {
        "search": {"description": "Search notes", "params": {"query": "text"}},
        "note": {"description": "Read a note (use full URL from search results)", "params": {"url": "note URL"}},
        "comments": {"description": "Comments of a note", "params": {"url": "note URL or id"}},
        "feed": {"description": "Home recommendations", "params": {}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "xiaohongshu.com", "xhslink.com")

    def health(self, context: "Context") -> SourceHealth:
        st = opencli_status()
        if st.installed and st.ready:
            self.active_backend = "browser-bridge"
            return SourceHealth("ok", "desktop browser bridge connected", self.active_backend)
        if _mcporter_xhs_ready():
            self.active_backend = "xhs-mcp"
            return SourceHealth(
                "warn",
                "xhs MCP server configured — login status not verified until first call",
                self.active_backend,
            )
        self.active_backend = None
        return SourceHealth(
            "off", "no XiaoHongShu backend",
            hint="desktop: install the browser bridge; server: configure the xhs MCP server "
                 "with manually exported cookies (halfiralens configure xhs_cookies)",
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        st = opencli_status()
        if st.installed and st.ready:
            return self._via_bridge(op, params)
        if _mcporter_xhs_ready():
            return self._via_mcp(op, params, context)
        raise SourceUnavailableError(
            "no XiaoHongShu backend available",
            hint="install the desktop browser bridge, or configure the xhs MCP server",
        )

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        result = self.fetch("note", {"url": url}, context)
        return result[0] if isinstance(result, list) and result else result

    # ------------------------------------------------------------ backends
    def _via_bridge(self, op: str, params: Dict[str, Any]) -> Any:
        if op == "search":
            self.require(params, "query")
            argv = ["opencli", "xiaohongshu", "search", params["query"], "-f", "yaml"]
        elif op == "note":
            self.require(params, "url")
            argv = ["opencli", "xiaohongshu", "note", params["url"], "-f", "yaml"]
        elif op == "comments":
            self.require(params, "url")
            argv = ["opencli", "xiaohongshu", "comments", params["url"], "-f", "yaml"]
        elif op == "feed":
            argv = ["opencli", "xiaohongshu", "feed", "-f", "yaml"]
        else:
            return super().fetch(op, params, None)  # type: ignore[arg-type]
        out = run_cli(argv, timeout=90)
        if not out.ok:
            raise SourceUnavailableError("xiaohongshu bridge call failed", detail=out.stderr[:300])
        return self._yaml_artifacts(out.stdout, op, "cli:opencli", params)

    def _via_mcp(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        timeout_ms = "120000"
        if op == "search":
            self.require(params, "query")
            expr = f'xiaohongshu.search_feeds(keyword: {json.dumps(params["query"])})'
        elif op == "note":
            self.require(params, "url")
            expr = f'xiaohongshu.get_feed_detail(feed_id: {json.dumps(params["url"])}, xsec_token: "")'
        elif op == "feed":
            expr = "xiaohongshu.get_homefeed()"
        else:
            raise SourceUnavailableError(f"operation '{op}' not supported on this backend")
        payload = run_cli_json(["mcporter", "call", expr, "--timeout", timeout_ms], timeout=150)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return Artifact(
            title=f"xiaohongshu {op}", url="https://www.xiaohongshu.com",
            source="xiaohongshu", kind="post", content=text[:30000],
            content_format="json", retrieval_method="mcp:xiaohongshu",
            discovered_from=f"search:{params['query']}" if op == "search" else None,
        )

    @staticmethod
    def _yaml_artifacts(text: str, op: str, method: str, params: Dict[str, Any]):
        try:
            import yaml

            data = yaml.safe_load(text)
        except Exception:
            data = None
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        found_via = f"search:{params.get('query')}" if op == "search" else None
        out = []
        for i in items:
            if not isinstance(i, dict):
                continue
            out.append(Artifact(
                title=str(i.get("title") or i.get("display_title") or ""),
                url=str(i.get("url") or i.get("note_url") or ""),
                source="xiaohongshu", kind="post",
                content=str(i.get("content") or i.get("desc") or "")[:4000],
                metadata={k: v for k, v in i.items() if k not in ("title", "display_title", "url", "note_url", "content", "desc")},
                retrieval_method=method, discovered_from=found_via,
            ))
        if out:
            return out
        return Artifact(
            title=f"xiaohongshu {op}", url="https://www.xiaohongshu.com",
            source="xiaohongshu", kind="post", content=text[:30000], retrieval_method=method,
        )
