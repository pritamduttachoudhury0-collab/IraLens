# -*- coding: utf-8 -*-
"""Direct HTTP transport for search pages and raw fetches (D-076).

`fetch_search_page` gives the free search engines a browser-independent
transport: a plain SSRF-safe HTTP GET with a response size cap and an overall
deadline. The browser engine becomes a *fallback*, not a requirement, so core
search works with no engine binary installed.

Every fetch goes through `security.safe_urlopen` (DNS-pinned, redirect-
validated, public-URL policy enforced). Response bodies are streamed in
chunks under both a byte cap and a wall-clock deadline, which defeats
slow-drip responses that send data just fast enough to never trip a per-read
timeout. HTTP failures are classified by status code instead of being
collapsed into one generic error.

This module never parses results — it returns bytes decoded to text and
classifies transport-level failures. Parsing stays in the engine modules so
it remains a pure function of the payload (fixture-testable offline).
"""

from __future__ import annotations

import time
import urllib.error
from typing import Dict, Optional

from .errors import ExtractionError, OperationTimeoutError, PageUnavailableError
from .security import safe_urlopen

#: Browser-like UA: some SERPs answer the default urllib UA with a bot wall.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_DEADLINE_SECONDS = 30
_READ_CHUNK = 64 * 1024


def read_body_with_deadline(resp, max_bytes: int, deadline: float) -> bytes:
    """Stream a response in chunks under an overall time budget and size cap.

    The socket timeout bounds any single blocking read; the deadline bounds
    the whole transfer (slow-drip protection).
    """
    chunks = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise OperationTimeoutError(
                "fetch exceeded its total time budget",
                hint="the server may be trickle-feeding data; retry later",
                detail="slow_response_deadline",
            )
        chunk = resp.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ExtractionError(
                f"response exceeded the {max_bytes // (1024 * 1024)} MB cap",
                detail="response_too_large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _classify_http_error(exc: urllib.error.HTTPError) -> PageUnavailableError:
    """Map an HTTP status onto a specific, honest error (message kept short)."""
    status = exc.code
    if status == 404:
        return PageUnavailableError("fetch failed (HTTP 404)", hint="check the URL",
                                    detail="http_404")
    if status in (401, 403):
        return PageUnavailableError(
            f"fetch denied (HTTP {status}); likely a bot check or login wall",
            hint="wait before retrying, or use another engine",
            detail=f"http_{status}",
        )
    if status == 429:
        return PageUnavailableError("fetch rate-limited (HTTP 429)",
                                    hint="wait a moment and retry", detail="http_429")
    if 500 <= status <= 599:
        return PageUnavailableError(f"fetch failed (HTTP {status})",
                                    hint="temporary; retry shortly", detail=f"http_{status}")
    return PageUnavailableError(f"fetch returned HTTP {status}", detail=f"http_{status}")


def fetch_url_text(
    url: str,
    *,
    timeout: float = 30,
    max_bytes: Optional[int] = None,
    deadline_seconds: Optional[float] = None,
    headers: Optional[Dict[str, str]] = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> str:
    """Fetch *url* over SSRF-safe HTTP and return the decoded body text.

    Raises:
      ValueError / SecurityBlockedError  — the URL is not a clean public target
        (propagated from the security layer; never retried or bypassed).
      PageUnavailableError — HTTP status problems, classified by code.
      OperationTimeoutError — the overall deadline expired (slow-drip guard).
      ExtractionError — the response exceeded the size cap.
    """
    request_headers = {"User-Agent": user_agent, "Accept": "*/*"}
    request_headers.update(headers or {})
    limit = max_bytes or DEFAULT_MAX_BYTES
    deadline = time.monotonic() + (
        deadline_seconds if deadline_seconds else DEFAULT_DEADLINE_SECONDS
    )
    try:
        with safe_urlopen(url, timeout=timeout, headers=request_headers) as resp:
            body = read_body_with_deadline(resp, limit, deadline)
    except urllib.error.HTTPError as exc:
        raise _classify_http_error(exc) from exc
    except (OperationTimeoutError, ExtractionError):
        raise
    except Exception as exc:
        # Policy blocks (ValueError / SecurityBlockedError) are not fetch
        # failures — they must surface unchanged so callers never "retry
        # around" the SSRF guard. Timeout-looking URLErrors are transient.
        if isinstance(exc, (ValueError,)):
            raise
        from .errors import SecurityBlockedError

        if isinstance(exc, SecurityBlockedError):
            raise
        detail = str(exc)
        low = detail.lower()
        if "timed out" in low or "timeout" in low:
            raise OperationTimeoutError("fetch timed out", detail="timeout") from exc
        raise PageUnavailableError("fetch failed", detail=detail[:300]) from exc
    return body.decode("utf-8", errors="replace")


def fetch_search_page(
    url: str,
    *,
    timeout: float = 30,
    max_bytes: Optional[int] = None,
    deadline_seconds: Optional[float] = None,
) -> str:
    """Fetch the raw HTML/text of a search-engine results page (D-076).

    The transport for free engines: no browser engine and no search API key
    involved. Exactly `fetch_url_text` with a browser-like User-Agent and a
    text-friendly Accept header; kept as a named entry point because the
    search stack (and its tests) depend on this being one stable function.
    """
    return fetch_url_text(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        deadline_seconds=deadline_seconds,
        headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
    )
