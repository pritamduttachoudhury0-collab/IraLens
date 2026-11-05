# -*- coding: utf-8 -*-
"""Registry of search backends. Engine name -> backend instance."""

from __future__ import annotations

from typing import Dict

from .base import SearchBackend
from .arxiv import ArxivBackend
from .bing import BingBackend
from .crossref import CrossrefBackend
from .duckduckgo import DuckDuckGoBackend
from .github import GitHubSearchBackend
from .gdelt import GdeltBackend
from .exa import ExaBackend
from .mojeek import MojeekBackend
from .news_rss import BingNewsBackend, GoogleNewsBackend
from .publisher_feeds import PublisherFeedsBackend
from .sitemaps import SitemapsBackend
from .paid_backup import PaidSearchBackupBackend
from .searxng import SearxngBackend
from .stackexchange import StackExchangeBackend
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
    # Mojeek is part of the default Settings.search_engines chain as a fallback
    # general engine (see settings.py). It is registered here like every other
    # backend; whether it runs is decided by the configured chain, not here.
    backends = (ExaBackend(), DuckDuckGoBackend(), SearxngBackend(), BingBackend(),
                WikipediaBackend(), PaidSearchBackupBackend(), MojeekBackend(),
                CrossrefBackend(), ArxivBackend(), StackExchangeBackend(),
                GitHubSearchBackend(), GdeltBackend(),
                BingNewsBackend(), GoogleNewsBackend(),
                PublisherFeedsBackend(), SitemapsBackend())
    return {b.name: b for b in backends}


__all__ = ["SearchBackend", "build_backends", "LEGACY_ALIASES"]
