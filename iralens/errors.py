# -*- coding: utf-8 -*-
"""Unified error taxonomy for IraLens.

Every failure surfaced to the controlling AI/user is one of these types,
regardless of which internal component produced it. Messages are safe to
display: credentials are scrubbed and internal implementation ancestry is
never mentioned. Developer-facing detail is kept in `detail`.
"""

from __future__ import annotations



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
        data = {"error": self.error_type, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
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
        "authentication_required": AuthRequiredError,
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
