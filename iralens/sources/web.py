# -*- coding: utf-8 -*-
"""Generic web page reading.

One capability, ordered backends (local-first):
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

import re
import threading
import time
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, TYPE_CHECKING

_DIRECT_META_LOCAL = threading.local()

from .. import content_guard
from ..cache import ResponseCache
from ..extract import extract_title, html_to_markdown, looks_like_pdf, pdf_to_text
from ..errors import (
    ExtractionError,
    IraLensError,
    OperationTimeoutError,
    PageUnavailableError,
    classify_http_status,
)
from ..model import Artifact
from ..security import normalize_public_http_url, public_message, safe_urlopen
from ..settings import Settings
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_WHITESPACE_RE = re.compile(r"\s+")
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_DEADLINE_SECONDS = 60
_READ_CHUNK = 64 * 1024
_ANTIBOT_SCAN_BYTES = 4096
_READER_BASE = "https://r.jina.ai/"

#: A client-rendered app shell is not page text. When the static read returns
#: one of these markers (or practically nothing) and mode is "auto", the read
#: retries through the browser engine, which executes the JavaScript.
_JS_SHELL_MARKERS = (
    "enable javascript", "javascript is required", "javascript is disabled",
    "you need to enable javascript", "must enable javascript",
    "this application requires javascript", "please enable js",
)
#: Below this many visible characters a *complete* page is implausible.
_JS_SHELL_MIN_CHARS = 150

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


def looks_like_js_shell(text: str) -> bool:
    """Heuristic: the static text looks like an app shell, not the page content.

    Two signals: an explicit "enable JavaScript" notice, or so little text that
    no real page could be this thin. False positives are cheap (one extra
    rendered read, and the static text is kept when rendering is not better).
    """
    compact = _WHITESPACE_RE.sub(" ", text or "").strip()
    if not compact:
        return True
    low = compact.casefold()
    if any(marker in low for marker in _JS_SHELL_MARKERS):
        return True
    return len(compact) < _JS_SHELL_MIN_CHARS


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
        raise _classify_http_error(exc, mode="static", layer="reader") from exc
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
    unchanged; only markup is run through the extractor.
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
    pdf_max_bytes: Optional[int] = None,
    ocr_enabled: bool = True,
    ocr_max_pages: int = 5,
    ocr_language: str = "eng",
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
    _DIRECT_META_LOCAL.last = None
    try:
        with safe_urlopen(safe_url, timeout=timeout,
                          headers={"User-Agent": _UA, "Accept": "text/html,application/pdf,*/*"}) as resp:
            raw_ct = (getattr(resp, "headers", {}) or {}).get("Content-Type", "")
            content_type = raw_ct.lower()
            resp_url = str(getattr(resp, "url", None) or (resp.geturl() if hasattr(resp, "geturl") else safe_url))
            resp_status = int(getattr(resp, "status", None) or (resp.getcode() if hasattr(resp, "getcode") else 200) or 200)
            _DIRECT_META_LOCAL.last = {
                "requested_url": safe_url,
                "final_url": resp_url,
                "status_code": resp_status,
                "content_type": raw_ct or "text/html",
            }
            path = urllib.parse.urlsplit(safe_url).path.lower()
            is_pdf_target = ("application/pdf" in content_type or path.endswith(".pdf")
                             or path.startswith("/pdf/"))
            # Text-heavy PDFs can exceed the general HTML limit, but remain
            # bounded by their own cap and the original wall-clock deadline.
            body = _read_body_with_deadline(
                resp, max(limit, pdf_max_bytes or limit) if is_pdf_target else limit, deadline
            )
    except urllib.error.HTTPError as exc:
        raise _classify_http_error(exc, mode="static", layer="origin") from exc
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
    if looks_like_pdf(body):
        # PDF bodies must not be decoded as UTF-8 binary noise. Scanned-page
        # OCR runs locally when its optional host tools are present; if missing
        # the PDF extractor returns an explicit typed error, not fake success.
        text, _info = pdf_to_text(body, ocr_enabled=ocr_enabled,
                                  ocr_max_pages=ocr_max_pages, ocr_language=ocr_language)
    elif _looks_like_html(body):
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


def _classify_http_status(status: int, *, mode: str = "", layer: str = "origin",
                          retry_after: Optional[float] = None) -> IraLensError:
    """Map an HTTP status code onto a specific, honest error.

    Thin wrapper over `errors.classify_http_status` so the web path and the
    other fetch paths (RSS, Reddit, source adapters) agree on one taxonomy.
    `layer` says who answered; `mode` says which retrieval mode is in use, so
    the hint never tells the caller to switch to the mode they already use.
    """
    return classify_http_status(status, layer=layer, mode=mode, retry_after=retry_after)


def _classify_http_error(exc: urllib.error.HTTPError, *, mode: str = "",
                         layer: str = "origin") -> IraLensError:
    """Map an HTTP status from the reader onto a specific, honest error."""
    retry_after = None
    headers = getattr(exc, "headers", None)
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except Exception:
            raw = None
        if raw:
            try:
                retry_after = float(str(raw).strip())
            except (TypeError, ValueError):
                retry_after = None
    return _classify_http_status(exc.code, mode=mode, layer=layer,
                                 retry_after=retry_after)


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
        """Static chain: direct first; remote reader only if opted in.

        Returns (markdown, method). Raises the last PageUnavailableError when
        every enabled static backend failed.
        """
        try:
            return read_direct(
                url,
                max_bytes=settings.read_max_bytes,
                pdf_max_bytes=settings.read_pdf_max_bytes,
                deadline_seconds=settings.read_total_timeout_seconds,
                ocr_enabled=settings.pdf_ocr_enabled,
                ocr_max_pages=settings.pdf_ocr_max_pages,
                ocr_language=settings.pdf_ocr_language,
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
        static_failure: Optional[IraLensError] = None
        static_artifact: Optional[Artifact] = None
        # Clear any transport metadata left by an earlier read on this thread.
        # The artifact builder no longer reads it, but nothing else should
        # inherit it either.
        _DIRECT_META_LOCAL.last = None

        if mode in ("auto", "static"):
            try:
                # The transport metadata of *this* read is captured explicitly.
                # It used to be read back from a thread-local that only the
                # uncached path populated, so a cache hit inherited the
                # previous page's final_url/status/content_type and reported
                # one page's content under another page's identity.
                retrieval: Dict[str, Any] = {}
                cached = cache.get(_PAGE_NS, {"url": safe_url}, settings.cache_page_ttl_seconds)
                if cached is not None:
                    value, age = cached
                    markdown = value["markdown"]
                    method = value.get("method", "direct")
                    retrieval = dict(value.get("retrieval") or {})
                    cache_info = {"status": "hit", "age_seconds": round(age, 1)}
                else:
                    markdown, method = self._fetch_static(safe_url, settings)
                    retrieval = dict(getattr(_DIRECT_META_LOCAL, "last", None) or {})
                    cache_info = {"status": "miss"}
                    # Only successful reads are cached; a flagged page is still
                    # cacheable because it is public, and the flags travel with it.
                    # The retrieval metadata is cached *with* the body so a hit
                    # can describe itself truthfully.
                    cache.put(_PAGE_NS, {"url": safe_url},
                              {"markdown": markdown, "method": method,
                               "retrieval": retrieval},
                              cacheable=bool(markdown.strip()))
                flags = content_guard.scan(markdown)
                # The cache keeps the full text; the budget is enforced when
                # serving, so different max_chars values share one entry.
                content, truncated = content_guard.truncate_text(markdown, max_chars)
                static_artifact = _static_artifact(
                    safe_url, content, cache=cache_info, flags=flags, truncated=truncated,
                    total_chars=len(markdown), max_chars=max_chars, method=method,
                    retrieval=retrieval)
            except (PageUnavailableError, ExtractionError, OperationTimeoutError) as exc:
                static_failure = exc
                if mode == "static":
                    raise

        # A static read that succeeded but produced an app shell (or almost no
        # text) has not read the page: the useful text is rendered client-side.
        # In auto mode the browser gets its turn automatically.
        shell_detected = static_artifact is not None and looks_like_js_shell(static_artifact.content)
        if static_artifact is not None and (mode == "static" or not shell_detected):
            return static_artifact

        if mode == "browser" or (mode == "auto" and (static_artifact is None or shell_detected)):
            try:
                # Browser reads are never cached: the browser may hold session state.
                engine = context.engine()
                nav_info = engine.navigate(safe_url)
                nav_dict: Dict[str, Any] = nav_info if isinstance(nav_info, dict) else {}
                status_code: Optional[int] = (
                    int(nav_dict["status_code"])
                    if isinstance(nav_dict.get("status_code"), int)
                    else None
                )
                if status_code is not None and status_code >= 400:
                    raise _classify_http_status(status_code, mode="browser")

                markdown = engine.markdown(max_chars=max_chars)
                current = engine.current_url_title()
                content = markdown if isinstance(markdown, str) else str(markdown)
                if not content.strip():
                    raise PageUnavailableError(
                        "the page contained no readable text",
                        detail="empty_response",
                    )
                if _is_soft_404(content):
                    raise PageUnavailableError(
                        "the page appears not to exist (soft 404)",
                        hint="the server answered 200 but served a not-found page",
                        detail="soft_404",
                    )

                final_url = str(
                    nav_dict.get("final_url")
                    or nav_dict.get("url")
                    or current.get("url")
                    or safe_url
                )
                title = str(
                    nav_dict.get("title")
                    or current.get("title")
                    or _title_from_markdown(content)
                    or final_url
                )
                content_type: Optional[str] = (
                    str(nav_dict["content_type"])
                    if isinstance(nav_dict.get("content_type"), str)
                    else None
                )
                redirected = bool(nav_dict.get("redirected", final_url != safe_url))
                redirect_chain = list(
                    nav_dict.get("redirect_chain")
                    or ([safe_url, final_url] if redirected else [safe_url])
                )
                warnings: List[str] = list(nav_dict.get("warnings") or [])
                sec_flags = content_guard.scan(content)
                if sec_flags:
                    warnings.extend(f"security:{f}" for f in sec_flags)

                metadata: Dict[str, Any] = {
                    "requested_url": safe_url,
                    "final_url": final_url,
                    "redirected": redirected,
                    "redirect_chain": redirect_chain,
                    "max_chars": max_chars,
                    "truncated": max_chars > 0 and len(content) >= max_chars,
                }
                if status_code is not None:
                    metadata["status_code"] = status_code
                if content_type is not None:
                    metadata["content_type"] = content_type

                provenance: Dict[str, Any] = {
                    "security_flags": sec_flags,
                    "cache": {"status": "bypass"},
                    "requested_url": safe_url,
                    "final_url": final_url,
                    "redirected": redirected,
                }
                if status_code is not None:
                    provenance["status_code"] = status_code
                if content_type is not None:
                    provenance["content_type"] = content_type

                if static_artifact is not None:
                    # Keep the (possibly richer) static text when rendering did
                    # not actually improve it, but say what happened.
                    metadata["static_fallback"] = "js_shell"
                    if len(content.strip()) <= len(static_artifact.content.strip()):
                        static_artifact.metadata.update(
                            {"static_fallback": "js_shell_attempted",
                             "fallback_result": "kept_static_text"})
                        static_artifact.provenance["fallback"] = {
                            "reason": "js_shell", "result": "kept_static_text"}
                        return static_artifact
                    provenance["fallback"] = {"reason": "js_shell", "result": "browser_text"}
                return Artifact(
                    title=title,
                    url=final_url,
                    requested_url=safe_url,
                    final_url=final_url,
                    status_code=status_code,
                    content_type=content_type,
                    warnings=warnings,
                    source="web",
                    kind="page",
                    content=content,
                    content_format="markdown",
                    retrieval_method="browser",
                    metadata=metadata,
                    provenance=provenance,
                )
            except Exception as browser_exc:
                if static_artifact is not None:
                    # The static read produced text; never turn a usable read into
                    # a failure because the optional renderer is unavailable.
                    static_artifact.metadata["static_fallback"] = "js_shell_unavailable"
                    static_artifact.metadata["fallback_error"] = public_message(browser_exc)[:200]
                    static_artifact.provenance["fallback"] = {
                        "reason": "js_shell", "result": "browser_unavailable",
                        "error": getattr(browser_exc, "error_type", type(browser_exc).__name__)}
                    return static_artifact
                if static_failure is not None:
                    # Report the *primary* reason the read failed (typically a
                    # fetch error), with the fallback failure attached as a hint.
                    # Raising the secondary error instead hides the real cause.
                    static_failure.hint = (
                        f"{static_failure.hint}; browser fallback also failed: "
                        f"{public_message(browser_exc)}")[:400].strip("; ")
                    raise static_failure from browser_exc
                raise
        # Unreachable with the documented modes; keeps an unknown mode from
        # silently returning None (an untyped failure would break the contract).
        raise PageUnavailableError(
            "could not read the page", hint="mode must be auto, static, or browser",
            detail="unknown_mode")  # pragma: no cover - defensive


_PAGE_NS = "page.v1"


def _static_artifact(url: str, markdown: str, *, cache: dict, flags=None,
                     truncated: bool = False, total_chars: int = 0,
                     max_chars: int = 0, method: str = "direct",
                     retrieval: Optional[Dict[str, Any]] = None) -> Artifact:
    """Build the artifact for a static read.

    `retrieval` carries the transport metadata of *this* read (final URL,
    status, content type). It is passed explicitly rather than read from a
    thread-local, because a cache hit never performs a fetch and would
    otherwise inherit the previous page's identity.
    """
    direct_meta = dict(retrieval or {})
    final_url = str(direct_meta.get("final_url") or url)
    status_code = direct_meta.get("status_code") if isinstance(direct_meta.get("status_code"), int) else None
    content_type = direct_meta.get("content_type") if isinstance(direct_meta.get("content_type"), str) else None
    redirected = final_url != url
    metadata: Dict[str, Any] = {
        "requested_url": url,
        "final_url": final_url,
        "redirected": redirected,
        "truncated": truncated,
    }
    if status_code is not None:
        metadata["status_code"] = status_code
    if content_type is not None:
        metadata["content_type"] = content_type
    if max_chars:
        metadata["max_chars"] = max_chars
    if truncated and total_chars:
        metadata["total_chars"] = total_chars
    sec_flags = list(flags or [])
    warnings = [f"security:{f}" for f in sec_flags]
    if truncated:
        warnings.append("content_truncated")
    prov: Dict[str, Any] = {
        "security_flags": sec_flags,
        "cache": cache,
        "requested_url": url,
        "final_url": final_url,
        "redirected": redirected,
        # A cache hit is a distinct provenance fact: the bytes were not
        # fetched during this call, and the consumer should know that.
        "cache_hit": bool((cache or {}).get("status") == "hit"),
    }
    if status_code is not None:
        prov["status_code"] = status_code
    if content_type is not None:
        prov["content_type"] = content_type
    return Artifact(
        title=_title_from_markdown(markdown) or final_url,
        url=final_url,
        requested_url=url,
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        warnings=warnings,
        source="web",
        kind="page",
        content=markdown,
        content_format="markdown",
        retrieval_method=method,
        metadata=metadata,
        provenance=prov,
    )


def _title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines()[:20]:
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip()
    return ""
