# -*- coding: utf-8 -*-
"""Registry of search backends. Engine name -> backend instance."""

from __future__ import annotations

from typing import Dict

from .base import SearchBackend
from .bing import BingBackend
from .duckduckgo import DuckDuckGoBackend
from .exa import ExaBackend

#: Legacy names from the HalfIraLens `search(backend=...)` API map to chains.
LEGACY_ALIASES: Dict[str, tuple] = {
    "semantic-search": ("semantic-search",),
    "browser-search": ("duckduckgo", "bing"),
}


def build_backends() -> Dict[str, SearchBackend]:
    backends = (ExaBackend(), DuckDuckGoBackend(), BingBackend())
    return {b.name: b for b in backends}


__all__ = ["SearchBackend", "build_backends", "LEGACY_ALIASES"]
