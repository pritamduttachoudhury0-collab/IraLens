# -*- coding: utf-8 -*-
"""Bilibili — video detail, search, hot lists.

Backends (per the capability-layer study): bili-cli first (read-only, no
login), browser-session bridge for subtitles. yt-dlp is deliberately NOT a
backend here — Bilibili risk-control blocks it.
"""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from ..errors import SourceUnavailableError
from ..model import Artifact
from ..proc import probe_command, run_cli
from ..security import host_matches
from ._opencli import opencli_status
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context


def _bili_ok() -> bool:
    return probe_command("bili", ["--version"], timeout=10, package="bili-cli").ok


def _parse_yamlish(text: str) -> Any:
    try:
        import yaml

        return yaml.safe_load(text)
    except Exception:
        return text


class BilibiliSource(Source):
    name = "bilibili"
    description = "Bilibili videos, search, subtitles"
    backends = ["bili-cli", "browser-bridge"]
    tier = 1
    operations = {
        "video": {"description": "Video detail (BV id or URL)", "params": {"id": "BVxxx or URL"}},
        "search": {"description": "Search videos", "params": {"query": "text", "limit": "default 5"}},
        "hot": {"description": "Hot videos", "params": {"limit": "default 10"}},
        "subtitles": {"description": "Subtitles via desktop browser bridge", "params": {"id": "BVxxx"}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "bilibili.com", "b23.tv")

    def health(self, context: "Context") -> SourceHealth:
        if _bili_ok():
            self.active_backend = "bili-cli"
            return SourceHealth("ok", "read-only CLI available (no login needed)", self.active_backend)
        st = opencli_status()
        if st.installed:
            self.active_backend = "browser-bridge"
            status = "ok" if st.ready else "warn"
            return SourceHealth(status, st.hint or "desktop browser bridge available", self.active_backend)
        self.active_backend = None
        return SourceHealth(
            "off", "no Bilibili backend installed",
            hint="npm i -g bili-cli  (read-only) — or install the desktop browser bridge",
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if not _bili_ok():
            raise SourceUnavailableError(
                "no Bilibili backend available", hint="npm i -g bili-cli"
            )
        if op == "video":
            self.require(params, "id")
            out = run_cli(["bili", "video", self._bv(params["id"])], timeout=30)
            if not out.ok:
                raise SourceUnavailableError("bilibili video lookup failed", detail=out.stderr[:300])
            data = _parse_yamlish(out.stdout)
            return Artifact(
                title=(data.get("title") if isinstance(data, dict) else "") or self._bv(params["id"]),
                url=f"https://www.bilibili.com/video/{self._bv(params['id'])}",
                source="bilibili", kind="video",
                content=out.stdout[:4000],
                content_format="text",
                metadata=data if isinstance(data, dict) else {},
                retrieval_method="cli:bili",
            )
        if op == "search":
            self.require(params, "query")
            limit = int(params.get("limit") or 5)
            out = run_cli(["bili", "search", params["query"], "--type", "video", "-n", str(limit)], timeout=30)
            if not out.ok:
                raise SourceUnavailableError("bilibili search failed", detail=out.stderr[:300])
            data = _parse_yamlish(out.stdout)
            items = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) else []) or []
            return [
                Artifact(
                    title=str(i.get("title", "")),
                    url=str(i.get("url") or i.get("link") or ""),
                    source="bilibili", kind="video",
                    content=str(i.get("description") or i.get("desc") or "")[:500],
                    metadata={k: v for k, v in i.items() if k not in ("title", "url", "link", "description", "desc")},
                    retrieval_method="cli:bili",
                    discovered_from=f"search:{params['query']}",
                )
                for i in items[:limit]
            ]
        if op == "hot":
            limit = int(params.get("limit") or 10)
            out = run_cli(["bili", "hot", "-n", str(limit)], timeout=30)
            if not out.ok:
                raise SourceUnavailableError("bilibili hot list failed", detail=out.stderr[:300])
            return Artifact(
                title="Bilibili hot videos", url="https://www.bilibili.com/v/popular/all",
                source="bilibili", kind="other", content=out.stdout[:6000],
                retrieval_method="cli:bili",
            )
        if op == "subtitles":
            self.require(params, "id")
            st = opencli_status()
            if not (st.installed and st.ready):
                raise SourceUnavailableError(
                    "subtitles need the desktop browser bridge with a connected session",
                    hint=st.hint,
                )
            out = run_cli(["opencli", "bilibili", "subtitle", self._bv(params["id"]), "-f", "yaml"], timeout=60)
            if not out.ok:
                raise SourceUnavailableError("subtitle fetch failed", detail=out.stderr[:300])
            return Artifact(
                title=f"Bilibili subtitles {self._bv(params['id'])}",
                url=f"https://www.bilibili.com/video/{self._bv(params['id'])}",
                source="bilibili", kind="transcript", content=out.stdout[:200000],
                retrieval_method="cli:opencli",
            )
        return super().fetch(op, params, context)

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        return self.fetch("video", {"id": url}, context)

    @staticmethod
    def _bv(id_or_url: str) -> str:
        import re

        match = re.search(r"(BV[\w]+|av\d+)", str(id_or_url))
        return match.group(1) if match else str(id_or_url)
