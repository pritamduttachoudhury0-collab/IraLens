# -*- coding: utf-8 -*-
"""Half IraLens — one unified Internet-access system.

Public API:

    from halfiralens import HalfIraLens

    with HalfIraLens() as hil:
        results = hil.search("rust headless browser", limit=5)
        page = hil.open(results[0].url)
        hil.fetch("github", "search_repos", query="cdp", limit=5)

Everything — web search, page reading, full browser interaction, and
specialized platform access — is one system with one data model, one error
taxonomy, and one session.
"""

from .core import HalfIraLens
from .errors import (
    AuthRequiredError,
    EngineUnavailableError,
    ExtractionError,
    HalfIraLensError,
    NavigationError,
    OperationTimeoutError,
    OperationUnsupportedError,
    PageUnavailableError,
    SecurityBlockedError,
    SessionStateError,
    SourceUnavailableError,
)
from .model import Artifact, SearchResult

__version__ = "0.1.0"

__all__ = [
    "HalfIraLens",
    "Artifact",
    "SearchResult",
    "HalfIraLensError",
    "PageUnavailableError",
    "NavigationError",
    "SourceUnavailableError",
    "AuthRequiredError",
    "OperationUnsupportedError",
    "OperationTimeoutError",
    "ExtractionError",
    "SecurityBlockedError",
    "EngineUnavailableError",
    "SessionStateError",
    "__version__",
]
