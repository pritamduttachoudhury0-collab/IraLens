# -*- coding: utf-8 -*-
"""Source registry — every specialized Internet source of Half IraLens.

URL routing works like the capability-layer study's `can_handle` mechanism:
the first source that natively handles a URL serves it; `web` is the
universal fallback. The registry order below is the routing priority.
"""

from __future__ import annotations

from typing import List, Optional

from .base import Source, SourceHealth
from .bilibili import BilibiliSource
from .boss import BossSource
from .github import GitHubSource
from .linkedin import LinkedInSource
from .reddit import RedditSource
from .rss import RSSSource
from .search import SearchSource
from .social_sites import FacebookSource, InstagramSource
from .transcribe import TranscribeSource
from .twitter import TwitterSource
from .v2ex import V2EXSource
from .web import WebSource
from .xiaohongshu import XiaoHongShuSource
from .xueqiu import XueqiuSource
from .youtube import YouTubeSource

ALL_SOURCES: List[Source] = [
    GitHubSource(),
    YouTubeSource(),
    TwitterSource(),
    RedditSource(),
    BilibiliSource(),
    XiaoHongShuSource(),
    V2EXSource(),
    XueqiuSource(),
    BossSource(),
    LinkedInSource(),
    FacebookSource(),
    InstagramSource(),
    TranscribeSource(),
    RSSSource(),
    SearchSource(),
    WebSource(),  # universal fallback — must stay last
]


def get_source(name: str) -> Optional[Source]:
    for source in ALL_SOURCES:
        if source.name == name:
            return source
    return None


def source_names() -> List[str]:
    return [s.name for s in ALL_SOURCES]


def route_url(url: str) -> Optional[Source]:
    """First source that natively reads this URL (excluding the web fallback
    unless it is the only option)."""
    for source in ALL_SOURCES:
        if source.name == "web":
            continue
        try:
            if source.can_handle(url):
                return source
        except Exception:
            continue
    return None


__all__ = [
    "Source",
    "SourceHealth",
    "ALL_SOURCES",
    "get_source",
    "source_names",
    "route_url",
]
