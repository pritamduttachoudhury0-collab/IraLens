# -*- coding: utf-8 -*-
"""Registry of search backends. Engine name -> backend instance."""

from __future__ import annotations

from typing import Dict

from .base import SearchBackend
from .bing import BingBackend
from .duckduckgo import DuckDuckGoBackend
from .exa import ExaBackend
from .mojeek import MojeekBackend
from .paid_backup import PaidSearchBackupBackend
from .searxng import SearxngBackend
from .wikipedia import WikipediaBackend

#: Legacy names from the IraLens `search(backend=...)` API map to chains.
LEGACY_ALIASES: Dict[str, tuple] = {
    "semantic-search": ("semantic-search",),
    # "browser-search" keeps its historical meaning: the public HTML search
    # endpoints. SearXNG is inserted only when the operator configured an
    # instance; no third-party public SearXNG host is used by default.
    "browser-search": ("duckduckgo", "bing"),
}


def build_backends() -> Dict[str, SearchBackend]:
    # Mojeek is registered so it can be enabled by name, but it is NOT in the
    # default Settings.search_engines chain (opt-in, not live-verified).
    backends = (ExaBackend(), DuckDuckGoBackend(), SearxngBackend(), BingBackend(),
                WikipediaBackend(), PaidSearchBackupBackend(), MojeekBackend())
    return {b.name: b for b in backends}


__all__ = ["SearchBackend", "build_backends", "LEGACY_ALIASES"]
