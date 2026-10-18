# -*- coding: utf-8 -*-
"""Unified error taxonomy for IraLens.

Every failure surfaced to the controlling AI/user is one of these types,
regardless of which internal component produced it. Messages are safe to
display: credentials are scrubbed and internal implementation ancestry is
never mentioned. Developer-facing detail is kept in `detail`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: Longest developer-facing `detail` preserved in structured output. Detail is
#: diagnostic, not a data channel; an unbounded string would make the JSON
#: envelope unusable for a programmatic caller.
MAX_DETAIL_CHARS = 500


def _scrub_detail(detail: str) -> str:
    """Redact credentials from a developer-facing detail string.

    The scrubber lives in `security` to keep every redaction rule in one
    place; the import is local so `errors` stays dependency-free.
    """
    if not detail:
        return ""
    try:
        from .security import scrub_url_credentials

        text = scrub_url_credentials(detail)
    except Exception:  # pragma: no cover - defensive; never mask the error
        text = str(detail)
    text = " ".join(str(text).split())
    if len(text) > MAX_DETAIL_CHARS:
        text = text[: MAX_DETAIL_CHARS - 1].rstrip() + "…"
    return text


class IraLensError(Exception):
    """Base class for all IraLens errors."""

    #: Stable machine-readable error type name.
    error_type: str = "error"

    def __init__(self, message: str, *, hint: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def to_dict(self) -> dict:
        """Structured form. `detail` is preserved (scrubbed) on purpose.

        Dropping `detail` (the historical behaviour) made every structured
        error envelope lossy: a programmatic caller could not tell a 403 from
        a 404, could not read a `Retry-After`, and could not decide whether a
        retry was worth attempting. The message stays safe to display; the
        detail carries the machine-readable cause.
        """
        data: Dict[str, Any] = {"error": self.error_type, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        detail = _scrub_detail(self.detail)
        if detail:
            data["detail"] = detail
        retry_after = getattr(self, "retry_after", None)
        if retry_after is not None:
            data["retry_after"] = retry_after
        return data

    def __str__(self) -> str:  # pragma: no cover - trivial
        text = f"{self.error_type}: {self.message}"
        if self.hint:
            text += f" (hint: {self.hint})"
        return text


class InvalidInputError(IraLensError):
    """The caller supplied a missing or malformed argument."""

    error_type = "invalid_input"


class PageUnavailableError(IraLensError):
    """The page could not be retrieved (404/403/network refused/empty)."""

    error_type = "page_unavailable"


class NavigationError(IraLensError):
    """Browser navigation failed (bad URL, blocked, crash)."""

    error_type = "navigation_failed"


class SourceUnavailableError(IraLensError):
    """A specialized source cannot serve this operation right now."""

    error_type = "source_unavailable"


class SearchEnginesFailedError(SourceUnavailableError):
    """Every search engine failed (blocked, unreachable, or broken).

    Distinct from an honest empty result: the query may be fine, the engines
    did not answer. The CLI exits 4 for this case so callers can tell it apart
    from success (0) and caller errors (2).
    """

    error_type = "search_engines_failed"


class RateLimitedError(PageUnavailableError):
    """The origin (or an upstream service) refused the request for rate.

    Distinct from a generic failure because the correct response is *wait*,
    not *retry elsewhere*: switching transport does not help a 429, and the
    `Retry-After` value (when the origin supplies one) is preserved so the
    caller can pace itself.

    It stays a subclass of `PageUnavailableError` so every existing
    `except PageUnavailableError` path keeps working; the machine-readable
    `error` field is the specific `rate_limited`.
    """

    error_type = "rate_limited"

    def __init__(self, message: str, *, hint: str = "", detail: str = "",
                 retry_after: Optional[float] = None) -> None:
        super().__init__(message, hint=hint, detail=detail)
        self.retry_after = retry_after


class AuthRequiredError(IraLensError):
    """The operation needs credentials/login the user has not provided."""

    error_type = "authentication_required"


class OperationUnsupportedError(IraLensError):
    """The requested operation is not supported by this system/source."""

    error_type = "operation_unsupported"


class OperationTimeoutError(IraLensError):
    """The operation exceeded its time budget."""

    error_type = "timeout"


class ExtractionError(IraLensError):
    """Content/selector extraction failed."""

    error_type = "extraction_failed"


class SecurityBlockedError(IraLensError):
    """Blocked by a security policy (SSRF guard, unsafe URL, bad scheme)."""

    error_type = "blocked_by_security_policy"


class EngineUnavailableError(IraLensError):
    """The browser engine is not installed/started."""

    error_type = "browser_engine_unavailable"


class SessionStateError(IraLensError):
    """Session/state operation failed (unknown tab, corrupted state…)."""

    error_type = "session_state_error"


def classify_http_status(
    status: int,
    *,
    layer: str = "origin",
    mode: str = "",
    retry_after: Optional[float] = None,
) -> IraLensError:
    """Map an HTTP status onto a specific, honest, mode-aware error.

    `layer` names *who answered*: ``"origin"`` (the site itself) or
    ``"reader"`` (a third-party reader service standing in for the origin).
    Naming the right layer matters: a 429 returned by the origin used to be
    reported as "the reader service is rate-limited", which blames the wrong
    component and points the caller at the wrong fix.

    `mode` is the retrieval mode already in use (``auto``, ``static``,
    ``browser``). A hint must never tell the caller to switch to the mode
    they are already using.

    The returned error is always typed: a 429 is a `RateLimitedError`, not a
    generic page failure, and the origin's `Retry-After` is preserved.
    """
    if status in (301, 302, 303, 307, 308):
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
        if layer == "reader":
            hint = ("the remote reader was denied; retry with mode=browser"
                    if mode != "browser"
                    else "the remote reader was denied too; try a public API")
        elif mode == "browser":
            hint = ("the site denied the rendered session as well; a logged-in "
                    "backend or a public API is required")
        elif mode == "static":
            hint = "the direct fetch was denied; retry with mode=browser"
        else:
            hint = "retry with mode=browser"
        return PageUnavailableError(
            f"the site denied anonymous access (HTTP {status}); likely a bot "
            "check or login wall",
            hint=hint,
            detail=f"http_{status}",
        )
    if status == 429:
        where = "the remote reader service" if layer == "reader" else "the site"
        hint = ("wait for the cooldown and retry"
                if retry_after
                else "wait a moment and retry; switching transport does not lift an origin rate limit")
        return RateLimitedError(
            f"{where} is rate-limiting the request (429)",
            hint=hint,
            detail="http_429",
            retry_after=retry_after,
        )
    if 500 <= status <= 599:
        where = "the reader service" if layer == "reader" else "the site"
        return PageUnavailableError(
            f"{where} failed (HTTP {status})",
            hint="temporary; retry shortly",
            detail=f"http_{status}",
        )
    where = "the reader" if layer == "reader" else "the site"
    return PageUnavailableError(f"{where} returned HTTP {status}",
                                detail=f"http_{status}")


def classify_error(
    error_type: str,
    message: str,
    *,
    hint: str = "",
    detail: str = "",
) -> IraLensError:
    """Build the right error subclass from a machine-readable type name."""
    mapping = {
        "page_unavailable": PageUnavailableError,
        "navigation_failed": NavigationError,
        "source_unavailable": SourceUnavailableError,
        "search_engines_failed": SearchEnginesFailedError,
        "authentication_required": AuthRequiredError,
        "rate_limited": RateLimitedError,
        "operation_unsupported": OperationUnsupportedError,
        "invalid_input": InvalidInputError,
        "timeout": OperationTimeoutError,
        "extraction_failed": ExtractionError,
        "blocked_by_security_policy": SecurityBlockedError,
        "browser_engine_unavailable": EngineUnavailableError,
        "session_state_error": SessionStateError,
    }
    cls = mapping.get(error_type, IraLensError)
    return cls(message, hint=hint, detail=detail)
