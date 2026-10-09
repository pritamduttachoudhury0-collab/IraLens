# -*- coding: utf-8 -*-
"""IraLens — one unified Internet-access system.

Public API:

    from iralens import IraLens

    with IraLens() as lens:
        results = lens.search("rust headless browser", limit=5)
        page = lens.open(results[0].url)
        lens.fetch("github", "search_repos", query="cdp", limit=5)

Everything — web search, page reading, full browser interaction, and
specialized platform access — is one system with one data model, one error
taxonomy, and one session.
"""

from .core import IraLens
from .errors import (
    AuthRequiredError,
    EngineUnavailableError,
    ExtractionError,
    IraLensError,
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
    "IraLens",
    "Artifact",
    "SearchResult",
    "IraLensError",
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
