# -*- coding: utf-8 -*-
"""Xueqiu (雪球) — stock quotes, search, hot content.

Served through the desktop browser bridge (the user's own logged-in Chrome
session). Quotes may be delayed; this is not investment advice.
"""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from ..errors import AuthRequiredError, SourceUnavailableError
from ..model import Artifact
from ..proc import run_cli
from ..security import host_matches
from ._opencli import opencli_status
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context


class XueqiuSource(Source):
    name = "xueqiu"
    description = "Xueqiu stock quotes and discussions"
    backends = ["browser-bridge"]
    tier = 2
    operations = {
        "whoami": {"description": "Verify login state", "params": {}},
        "search": {"description": "Search stocks", "params": {"query": "text"}},
        "stock": {"description": "Real-time quote", "params": {"symbol": "e.g. NVDA"}},
        "hot": {"description": "Hot content", "params": {}},
        "hot_stock": {"description": "Hot stocks", "params": {}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "xueqiu.com")

    def health(self, context: "Context") -> SourceHealth:
        st = opencli_status()
        if st.installed and st.ready:
            self.active_backend = "browser-bridge"
            return SourceHealth("ok", "desktop browser bridge connected", self.active_backend)
        if st.installed:
            self.active_backend = None
            return SourceHealth("warn", st.hint or "bridge installed but extension not connected", None)
        self.active_backend = None
        return SourceHealth(
            "off", "no Xueqiu backend",
            hint="install the desktop browser bridge and log in to xueqiu.com in Chrome",
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        st = opencli_status()
        if not (st.installed and st.ready):
            raise SourceUnavailableError(
                "Xueqiu needs the desktop browser bridge with a connected, logged-in session",
                hint=st.hint,
            )
        mapping = {
            "whoami": ["opencli", "xueqiu", "whoami", "-f", "yaml"],
            "search": ["opencli", "xueqiu", "search", str(params.get("query", "")), "-f", "yaml"],
            "stock": ["opencli", "xueqiu", "stock", str(params.get("symbol", "")), "-f", "yaml"],
            "hot": ["opencli", "xueqiu", "hot", "-f", "yaml"],
            "hot_stock": ["opencli", "xueqiu", "hot-stock", "-f", "yaml"],
        }
        if op not in mapping:
            return super().fetch(op, params, context)
        if op == "search":
            self.require(params, "query")
        if op == "stock":
            self.require(params, "symbol")
        out = run_cli(mapping[op], timeout=60)
        if not out.ok:
            err = (out.stderr or out.stdout or "").strip()
            # HTTP 400 is usually session/cookie trouble — not "stock does not exist".
            if "400" in err or "auth" in err.lower():
                raise AuthRequiredError(
                    "Xueqiu session problem (not a missing symbol)",
                    hint="re-login in Chrome, or import the minimal xq_a_token cookie",
                    detail=err[:300],
                )
            raise SourceUnavailableError("xueqiu call failed", detail=err[:300])
        return Artifact(
            title=f"Xueqiu {op}", url="https://xueqiu.com",
            source="xueqiu", kind="other", content=out.stdout[:20000],
            retrieval_method="cli:opencli",
            metadata={"delayed_quotes": True},
            discovered_from=f"search:{params.get('query')}" if op == "search" else None,
        )
