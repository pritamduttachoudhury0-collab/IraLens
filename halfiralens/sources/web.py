# -*- coding: utf-8 -*-
"""Generic web page reading.

One capability, ordered backends:
  1. static-reader — a remote markdown reader service (fast, no JS).
     Adapted from the capability-layer source's reader implementation,
     including anti-bot challenge detection and response caps.
  2. browser — the shared engine renders the page (full JS, local).

`read()` walks the chain; anti-bot walls or fetch failures fall through to
the engine automatically.
"""

from __future__ import annotations

import urllib.request
from typing import Any, Dict, TYPE_CHECKING

from ..errors import ExtractionError, PageUnavailableError
from ..model import Artifact
from ..security import normalize_public_http_url
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_ANTIBOT_SCAN_BYTES = 4096
_READER_BASE = "https://r.jina.ai/"


def _is_antibot_page(body: bytes) -> bool:
    """Recognize high-confidence reader/Cloudflare challenge responses."""
    sample = body[:_ANTIBOT_SCAN_BYTES].decode("utf-8", errors="ignore").casefold()
    captcha_warning = "warning:" in sample and "requiring captcha" in sample
    challenge_structure = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "## performing security verification",
            "title: attention required! | cloudflare",
        )
    )
    cloudflare_block = "title: attention required! | cloudflare" in sample and (
        "ray id" in sample or "/cdn-cgi/challenge-platform/" in sample
    )
    return (captcha_warning and challenge_structure) or cloudflare_block


def read_with_static_reader(url: str, timeout: int = 30) -> str:
    """Fetch markdown for *url* through the remote reader service."""
    safe_url = normalize_public_http_url(url)
    req = urllib.request.Request(
        _READER_BASE + safe_url,
        headers={"User-Agent": _UA, "Accept": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(_MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise PageUnavailableError("static reader could not fetch the page", detail=str(exc)) from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ExtractionError("reader response exceeded the 5 MB cap")
    if _is_antibot_page(body):
        raise PageUnavailableError(
            "the site served an anti-bot challenge to the static reader",
            hint="retry with the browser backend (open with mode=browser)",
        )
    return body.decode("utf-8", errors="replace")


class WebSource(Source):
    name = "web"
    description = "Any public web page"
    backends = ["static-reader", "browser"]
    tier = 0
    operations = {
        "read": {
            "description": "Read any public URL as markdown/text",
            "params": {"url": "page URL", "mode": "auto|static|browser (default auto)"},
        }
    }

    def can_handle(self, url: str) -> bool:
        return True  # universal fallback source

    def health(self, context: "Context") -> SourceHealth:
        self.active_backend = self.backends[0]
        return SourceHealth(
            "ok",
            "static reader always available; browser backend per engine status",
            self.active_backend,
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op != "read":
            return super().fetch(op, params, context)
        url = params.get("url")
        if not url:
            raise ExtractionError("web read requires 'url'")
        return self.read_url(url, context, mode=params.get("mode", "auto"))

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        safe_url = normalize_public_http_url(url)

        if mode in ("auto", "static"):
            try:
                markdown = read_with_static_reader(safe_url)
                title = _title_from_markdown(markdown) or safe_url
                return Artifact(
                    title=title,
                    url=safe_url,
                    source="web",
                    kind="page",
                    content=markdown,
                    content_format="markdown",
                    retrieval_method="static-reader",
                )
            except PageUnavailableError:
                if mode == "static":
                    raise
            except ExtractionError:
                if mode == "static":
                    raise

        # Browser backend (also the only path for mode="browser").
        engine = context.engine()
        engine.navigate(safe_url)
        markdown = engine.markdown(max_chars=20000)
        current = engine.current_url_title()
        content = markdown if isinstance(markdown, str) else str(markdown)
        return Artifact(
            title=current.get("title") or _title_from_markdown(content) or safe_url,
            url=current.get("url") or safe_url,
            source="web",
            kind="page",
            content=content,
            content_format="markdown",
            retrieval_method="browser",
        )


def _title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines()[:20]:
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip()
    return ""
