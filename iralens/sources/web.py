# -*- coding: utf-8 -*-
"""Generic web page reading.

One capability, ordered backends (local-first, D-077):
  1. direct -- SSRF-safe HTTP fetch + stdlib HTML extraction (local, private).
     This is the default; no URL leaves the machine except to the target site.
  2. static-reader -- a remote markdown reader service (r.jina.ai). OFF by
     default; opt in with `read_remote_reader_enabled` /
     `IRALENS_READ_REMOTE_READER_ENABLED`.
  3. browser -- the shared engine renders the page (full JS, local).

`read()` walks the chain; anti-bot walls or fetch failures fall through to the
next backend automatically. Every fetch carries redirect validation
(SSRF-safe), an overall read deadline (slow-drip protection), a response size
cap, HTTP status classification, and soft-404 detection. `max_chars` is
enforced on all backends, and the artifact says when its content was truncated.
"""

from __future__ import annotations

import time
import urllib.error
from typing import Any, Dict, Optional, TYPE_CHECKING

from .. import content_guard
from ..cache import ResponseCache
from ..extract import extract_title, html_to_markdown
from ..errors import ExtractionError, OperationTimeoutError, PageUnavailableError
from ..model import Artifact
from ..security import normalize_public_http_url, safe_urlopen
from ..settings import Settings
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_DEADLINE_SECONDS = 60
_READ_CHUNK = 64 * 1024
_ANTIBOT_SCAN_BYTES = 4096
_READER_BASE = "https://r.jina.ai/"

#: Soft-404 heuristic: a *short* body whose head looks like a not-found page.
#: The length bound keeps real articles that merely mention "404" intact.
_SOFT_404_MARKERS = ("404 not found", "page not found", "error 404", "does not exist")
_SOFT_404_MAX_LEN = 2000


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


def _is_soft_404(text: str) -> bool:
    if len(text) > _SOFT_404_MAX_LEN:
        return False
    head = text[:1024].casefold()
    return any(marker in head for marker in _SOFT_404_MARKERS)


def _read_body_with_deadline(resp, max_bytes: int, deadline: float) -> bytes:
    """Stream a response in chunks under an overall time budget and size cap.

    The socket timeout bounds any single blocking read; the deadline bounds
    the whole transfer, which defeats slow-drip responses that send data just
    fast enough to never trip a per-read timeout.
    """
    chunks = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise OperationTimeoutError(
                "page read exceeded its total time budget",
                hint="the server may be trickle-feeding data; retry later",
                detail="slow_response_deadline",
            )
        chunk = resp.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ExtractionError(
                f"page response exceeded the {max_bytes // (1024 * 1024)} MB cap",
                detail="response_too_large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def read_with_static_reader(
    url: str,
    timeout: int = 30,
    *,
    max_bytes: Optional[int] = None,
    deadline_seconds: Optional[float] = None,
) -> str:
    """Fetch markdown for *url* through the remote reader service.

    The fetch goes through `safe_urlopen`: every redirect hop is re-checked
    against the public-URL policy and connections are pinned to validated
    addresses. HTTP failures are classified by status code instead of being
    collapsed into one generic error.
    """
    safe_url = normalize_public_http_url(url)
    limit = max_bytes or _DEFAULT_MAX_BYTES
    deadline = time.monotonic() + (deadline_seconds if deadline_seconds else _DEFAULT_DEADLINE_SECONDS)
    try:
        with safe_urlopen(_READER_BASE + safe_url, timeout=timeout,
                          headers={"User-Agent": _UA, "Accept": "text/plain"}) as resp:
            body = _read_body_with_deadline(resp, limit, deadline)
    except urllib.error.HTTPError as exc:
        raise _classify_http_error(exc) from exc
    except (OperationTimeoutError, ExtractionError):
        raise
    except Exception as exc:
        from ..errors import SecurityBlockedError

        if isinstance(exc, SecurityBlockedError):
            raise
        raise PageUnavailableError(
            "static reader could not fetch the page", detail=str(exc)
        ) from exc
    if _is_antibot_page(body):
        raise PageUnavailableError(
            "the site served an anti-bot challenge to the static reader",
            hint="retry with the browser backend (open with mode=browser)",
            detail="antibot_challenge",
        )
    text = body.decode("utf-8", errors="replace")
    if not text.strip():
        raise PageUnavailableError("the reader returned an empty page",
                                   detail="empty_response")
    if _is_soft_404(text):
        raise PageUnavailableError(
            "the page appears not to exist (soft 404)",
            hint="the server answered 200 but served a not-found page",
            detail="soft_404",
        )
    return text


def _looks_like_html(body: bytes) -> bool:
    """Sniff the body: HTML/XML-ish markup vs plain text (markdown, JSON...).

    Plain-text bodies (including remote-reader markdown) pass through
    unchanged; only markup is run through the extractor (D-077).
    """
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html", b"<head", b"<body", b"<?xml",
                            b"<div", b"<span", b"<article", b"<main", b"<nav",
                            b"<section", b"<h1", b"<h2", b"<p", b"<ul", b"<table",
                            b"<!--"))


def read_direct(
    url: str,
    timeout: int = 30,
    *,
    max_bytes: Optional[int] = None,
    deadline_seconds: Optional[float] = None,
) -> str:
    """Fetch *url* directly and return markdown (or plain text) -- local-first.

    No third-party reader is involved: the page is fetched with `safe_urlopen`
    (every redirect hop re-validated, DNS-pinned) and converted with the stdlib
    extractor. HTML bodies become markdown; non-HTML bodies pass through as
    text. HTTP failures are classified by status code, and anti-bot challenge
    pages, soft-404s, oversized bodies, and slow-drip responses are rejected
    exactly as the remote reader path rejects them.
    """
    safe_url = normalize_public_http_url(url)
    limit = max_bytes or _DEFAULT_MAX_BYTES
    deadline = time.monotonic() + (deadline_seconds if deadline_seconds else _DEFAULT_DEADLINE_SECONDS)
    try:
        with safe_urlopen(safe_url, timeout=timeout,
                          headers={"User-Agent": _UA, "Accept": "text/html,*/*"}) as resp:
            body = _read_body_with_deadline(resp, limit, deadline)
    except urllib.error.HTTPError as exc:
        raise _classify_http_error(exc) from exc
    except (OperationTimeoutError, ExtractionError):
        raise
    except Exception as exc:
        from ..errors import SecurityBlockedError

        if isinstance(exc, SecurityBlockedError):
            raise
        raise PageUnavailableError(
            "direct fetch could not reach the page", detail=str(exc)
        ) from exc
    if _is_antibot_page(body):
        raise PageUnavailableError(
            "the site served an anti-bot challenge",
            hint="retry with the browser backend (open with mode=browser)",
            detail="antibot_challenge",
        )
    if _looks_like_html(body):
        html = body.decode("utf-8", errors="replace")
        text = html_to_markdown(html)
        title = extract_title(html)
        if title and not any(line.startswith("# ") for line in text.splitlines()[:5]):
            text = f"Title: {title}\n\n{text}" if text else f"Title: {title}"
    else:
        text = body.decode("utf-8", errors="replace")
    if not text.strip():
        raise PageUnavailableError("the page contained no readable text",
                                   detail="empty_response")
    if _is_soft_404(text):
        raise PageUnavailableError(
            "the page appears not to exist (soft 404)",
            hint="the server answered 200 but served a not-found page",
            detail="soft_404",
        )
    return text


def _classify_http_error(exc: urllib.error.HTTPError) -> PageUnavailableError:
    """Map an HTTP status from the reader onto a specific, honest error."""
    status = exc.code
    if status in (301, 302, 303, 307, 308):
        # A redirect status only surfaces as an error when the redirect
        # machinery gave up (e.g. a single-URL loop hit its repeat cap).
        return PageUnavailableError(
            "too many redirects (redirect loop suspected)",
            hint="the page keeps redirecting; try a different URL",
            detail="redirect_loop",
        )
    if status == 404:
        return PageUnavailableError(
            "page not found (404)", hint="check the URL for typos", detail="http_404"
        )
    if status in (401, 403):
        return PageUnavailableError(
            f"the site denied anonymous access (HTTP {status}); likely a bot check "
            "or login wall",
            hint="retry with mode=browser",
            detail=f"http_{status}",
        )
    if status == 429:
        return PageUnavailableError(
            "the reader service is rate-limited (429)",
            hint="wait a moment and retry, or use mode=browser",
            detail="http_429",
        )
    if 500 <= status <= 599:
        return PageUnavailableError(
            f"the reader service failed (HTTP {status})",
            hint="temporary; retry shortly",
            detail=f"http_{status}",
        )
    return PageUnavailableError(f"the reader returned HTTP {status}",
                                detail=f"http_{status}")


class WebSource(Source):
    name = "web"
    description = "Any public web page"
    backends = ["direct", "static-reader", "browser"]
    tier = 0
    operations = {
        "read": {
            "description": "Read any public URL as markdown/text",
            "params": {"url": "page URL", "mode": "auto|static|browser (default auto)",
                       "max_chars": "content budget in characters (default 20000)"},
        }
    }

    def can_handle(self, url: str) -> bool:
        return True  # universal fallback source

    def health(self, context: "Context") -> SourceHealth:
        self.active_backend = self.backends[0]
        settings = Settings.from_config(context.config)
        note = "local-first direct extraction always available"
        if settings.read_remote_reader_enabled:
            note += "; remote reader enabled (URLs are shared with r.jina.ai)"
        return SourceHealth("ok", note + "; browser backend per engine status",
                            self.active_backend)

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op != "read":
            return super().fetch(op, params, context)
        url = params.get("url")
        if not url:
            raise ExtractionError("web read requires 'url'")
        max_chars = int(params.get("max_chars") or 20000)
        return self.read_url(url, context, mode=params.get("mode", "auto"),
                             max_chars=max_chars)

    def _fetch_static(self, url: str, settings: Settings):
        """Static chain: direct first (D-077); remote reader only if opted in.

        Returns (markdown, method). Raises the last PageUnavailableError when
        every enabled static backend failed.
        """
        try:
            return read_direct(
                url,
                max_bytes=settings.read_max_bytes,
                deadline_seconds=settings.read_total_timeout_seconds,
            ), "direct"
        except (PageUnavailableError, OperationTimeoutError, ExtractionError):
            if not settings.read_remote_reader_enabled:
                raise
        markdown = read_with_static_reader(
            url,
            max_bytes=settings.read_max_bytes,
            deadline_seconds=settings.read_total_timeout_seconds,
        )
        return markdown, "static-reader"

    def read_url(self, url: str, context: "Context", mode: str = "auto",
                 max_chars: int = 20000) -> Artifact:
        safe_url = normalize_public_http_url(url)
        settings = Settings.from_config(context.config)
        cache = ResponseCache(enabled=settings.cache_enabled)

        if mode in ("auto", "static"):
            try:
                cached = cache.get(_PAGE_NS, {"url": safe_url}, settings.cache_page_ttl_seconds)
                if cached is not None:
                    value, age = cached
                    markdown = value["markdown"]
                    method = value.get("method", "direct")
                    cache_info = {"status": "hit", "age_seconds": round(age, 1)}
                else:
                    markdown, method = self._fetch_static(safe_url, settings)
                    cache_info = {"status": "miss"}
                    # Only successful reads are cached; a flagged page is still
                    # cacheable because it is public, and the flags travel with it.
                    cache.put(_PAGE_NS, {"url": safe_url},
                              {"markdown": markdown, "method": method},
                              cacheable=bool(markdown.strip()))
                flags = content_guard.scan(markdown)
                # The cache keeps the full text; the budget is enforced when
                # serving, so different max_chars values share one entry.
                content, truncated = content_guard.truncate_text(markdown, max_chars)
                return _static_artifact(safe_url, content, cache=cache_info, flags=flags,
                                        truncated=truncated, total_chars=len(markdown),
                                        max_chars=max_chars, method=method)
            except (PageUnavailableError, ExtractionError, OperationTimeoutError):
                if mode == "static":
                    raise

        # Browser backend (also the only path for mode="browser").
        # Browser reads are never cached: the browser may hold session state.
        engine = context.engine()
        engine.navigate(safe_url)
        markdown = engine.markdown(max_chars=max_chars)
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
            metadata={"max_chars": max_chars,
                      "truncated": max_chars > 0 and len(content) >= max_chars},
            provenance={"security_flags": content_guard.scan(content), "cache": {"status": "bypass"}},
        )


_PAGE_NS = "page.v1"


def _static_artifact(url: str, markdown: str, *, cache: dict, flags=None,
                     truncated: bool = False, total_chars: int = 0,
                     max_chars: int = 0, method: str = "direct") -> Artifact:
    metadata: Dict[str, Any] = {"truncated": truncated}
    if max_chars:
        metadata["max_chars"] = max_chars
    if truncated and total_chars:
        metadata["total_chars"] = total_chars
    return Artifact(
        title=_title_from_markdown(markdown) or url,
        url=url,
        source="web",
        kind="page",
        content=markdown,
        content_format="markdown",
        retrieval_method=method,
        metadata=metadata,
        provenance={"security_flags": list(flags or []), "cache": cache},
    )


def _title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines()[:20]:
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip()
    return ""
