# -*- coding: utf-8 -*-
"""RSS/Atom feeds via feedparser (local parsing, no external service)."""

from __future__ import annotations

import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ..errors import (
    ExtractionError,
    OperationTimeoutError,
    PageUnavailableError,
    classify_http_status,
)
from ..model import Artifact
from ..security import normalize_public_http_url, safe_urlopen
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

#: Hard cap on a feed download. A feed is metadata, not a document; a
#: multi-hundred-megabyte "feed" is an attack, not a subscription.
_MAX_FEED_BYTES = 8 * 1024 * 1024


def _canonical_entry_link(entry: Any, feed_link: str, feed_url: str,
                          warnings: List[str]) -> str:
    """The entry's canonical, absolute permalink.

    A relative `<link>` is resolved against the feed's own `<link>` (the
    site's root), not against the feed document URL, because a feed URL
    like ``https://site.example/feed.xml`` would turn ``/posts/1`` into
    ``https://site.example/posts/1`` only by accident of the path, and
    ``posts/1`` into ``https://site.example/feed/posts/1`` otherwise.
    A `guid` that is a URL is the next best answer.
    """
    raw = str(entry.get("link") or "").strip()
    if not raw:
        guid = str(entry.get("id") or "").strip()
        if guid.lower().startswith(("http://", "https://")):
            raw = guid
    if not raw:
        warnings.append("entry has no link and no URL guid; the permalink is unknown")
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        warnings.append(f"entry link is not a valid URL: {raw[:120]!r}")
        return raw
    if parsed.scheme and parsed.netloc:
        return raw
    if raw.startswith("//"):
        return "https:" + raw
    base = feed_link if str(feed_link).lower().startswith(("http://", "https://")) else feed_url
    try:
        joined = urllib.parse.urljoin(base, raw)
    except ValueError:
        warnings.append(f"entry link could not be resolved: {raw[:120]!r}")
        return raw
    if not joined:
        warnings.append(f"entry link could not be resolved: {raw[:120]!r}")
    return joined


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

    def read_url(self, url: str, context: "Context", mode: str = "auto", limit: int = 20) -> Artifact:
        import feedparser

        safe_url = normalize_public_http_url(url)
        status_code: Optional[int] = None
        final_url = safe_url
        content_type = ""
        retry_after: Optional[float] = None
        try:
            # The SSRF-safe transport is used here too: a plain HTTP client
            # re-resolves the host at connect time and follows redirects
            # without re-validating them, which is a private-network
            # reachability hole this source must not have.
            with safe_urlopen(safe_url, timeout=30,
                              headers={"User-Agent": _UA}) as resp:
                status_code = int(getattr(resp, "status", None)
                                  or (resp.getcode() if hasattr(resp, "getcode") else 200) or 200)
                final_url = str(getattr(resp, "url", None) or safe_url)
                headers = getattr(resp, "headers", {}) or {}
                content_type = str(headers.get("Content-Type") or "")
                raw_retry = headers.get("Retry-After")
                if raw_retry:
                    try:
                        retry_after = float(str(raw_retry).strip())
                    except (TypeError, ValueError):
                        retry_after = None
                body = resp.read(_MAX_FEED_BYTES)
        except urllib.error.HTTPError as exc:
            # A 429 is rate limiting, not "the feed is missing": the status
            # is preserved, typed, and the origin's Retry-After travels with it.
            raise classify_http_status(exc.code, layer="origin") from exc
        except (OperationTimeoutError, ExtractionError):
            raise
        except Exception as exc:
            raise PageUnavailableError("could not download the feed", detail=str(exc)) from exc
        if status_code is not None and status_code >= 400:
            raise classify_http_status(status_code, layer="origin",
                                       retry_after=retry_after)

        parsed = feedparser.parse(body)
        if parsed.bozo and not parsed.entries and not parsed.feed.get("title"):
            raise ExtractionError(
                "feed could not be parsed",
                detail=str(getattr(parsed, "bozo_exception", ""))[:200],
            )

        feed = parsed.feed
        title = feed.get("title") or safe_url
        feed_link = feed.get("link", "") or ""
        entries = parsed.entries[:limit]
        lines = [f"# {title}", ""]
        entry_records: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for i, entry in enumerate(entries, 1):
            entry_title = entry.get("title", "(untitled)")
            link = _canonical_entry_link(entry, feed_link, safe_url, warnings)
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
            entry_records.append(
                {
                    "title": entry.get("title", ""),
                    "url": link,
                    "published": entry.get("published", entry.get("updated", "")),
                    "guid": entry.get("id", ""),
                    "author": entry.get("author", ""),
                }
            )

        metadata = {
            "feed_title": title,
            "feed_link": feed_link,
            "entry_count": len(parsed.entries),
            "returned": len(entries),
            "entries": entry_records,
        }
        if warnings:
            # A malformed entry must not disappear silently: the feed said
            # N entries and the caller gets N-1, so say which one and why.
            metadata["entry_warnings"] = warnings
            metadata["entry_warning_count"] = len(warnings)
        return Artifact(
            title=title,
            url=final_url,
            source="rss",
            kind="feed",
            content="\n".join(lines),
            content_format="markdown",
            metadata=metadata,
            retrieval_method="local:feedparser",
            requested_url=safe_url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type or None,
        )
