# -*- coding: utf-8 -*-
"""RSS/Atom feeds via feedparser (local parsing, no external service)."""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from ..errors import ExtractionError, PageUnavailableError
from ..model import Artifact
from ..security import normalize_public_http_url
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class RSSSource(Source):
    name = "rss"
    description = "RSS/Atom feeds"
    backends = ["feedparser"]
    tier = 0
    operations = {
        "read": {
            "description": "Read a feed and return its entries",
            "params": {"url": "feed URL", "limit": "max entries (default 20)"},
        }
    }

    def can_handle(self, url: str) -> bool:
        lowered = (url or "").lower()
        return any(marker in lowered for marker in ("/feed", "/rss", ".xml", "atom", "rss."))

    def health(self, context: "Context") -> SourceHealth:
        try:
            import feedparser  # noqa: F401
        except ImportError:
            self.active_backend = None
            return SourceHealth("off", "feed parser library missing", hint="pip install feedparser")
        self.active_backend = self.backends[0]
        return SourceHealth("ok", "can read RSS/Atom feeds", self.active_backend)

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op != "read":
            return super().fetch(op, params, context)
        url = params.get("url")
        if not url:
            raise ExtractionError("rss read requires 'url'")
        return self.read_url(url, context, limit=int(params.get("limit") or 20))

    def read_url(self, url: str, context: "Context", limit: int = 20) -> Artifact:
        import feedparser
        import requests

        safe_url = normalize_public_http_url(url)
        try:
            resp = requests.get(safe_url, headers={"User-Agent": _UA}, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            raise PageUnavailableError("could not download the feed", detail=str(exc)) from exc

        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries and not parsed.feed.get("title"):
            raise ExtractionError(
                "feed could not be parsed",
                detail=str(getattr(parsed, "bozo_exception", ""))[:200],
            )

        feed = parsed.feed
        title = feed.get("title") or safe_url
        entries = parsed.entries[:limit]
        lines = [f"# {title}", ""]
        for i, entry in enumerate(entries, 1):
            entry_title = entry.get("title", "(untitled)")
            link = entry.get("link", "")
            published = entry.get("published", entry.get("updated", ""))
            lines.append(f"{i}. {entry_title}")
            if link:
                lines.append(f"   {link}")
            if published:
                lines.append(f"   published: {published}")
            summary = (entry.get("summary") or "").strip()
            if summary:
                lines.append(f"   {summary[:300]}")
            lines.append("")

        metadata = {
            "feed_title": title,
            "feed_link": feed.get("link", ""),
            "entry_count": len(parsed.entries),
            "returned": len(entries),
            "entries": [
                {
                    "title": e.get("title", ""),
                    "url": e.get("link", ""),
                    "published": e.get("published", e.get("updated", "")),
                }
                for e in entries
            ],
        }
        return Artifact(
            title=title,
            url=safe_url,
            source="rss",
            kind="feed",
            content="\n".join(lines),
            content_format="markdown",
            metadata=metadata,
            retrieval_method="local:feedparser",
        )
