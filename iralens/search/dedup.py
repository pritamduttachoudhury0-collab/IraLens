# -*- coding: utf-8 -*-
"""Deduplication of normalized hits into one result per page.

Pass 1 merges hits whose canonical URLs match (see urls.canonical_url).
Pass 2 merges near-duplicates: same registrable host and title token Jaccard
similarity at or above the threshold. Every decision goes into the returned
log so the caller can see why two hits were merged.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..search.schema import SearchHit
from .urls import canonical_url, registrable_host

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

#: Path segments that look like a version: `v2`, `3.10`, `version-1.2`, `1_2_3`.
_VERSION_SEG_RE = re.compile(r"^(?:v(?:er(?:sion)?)?[-_.]?)?\d+(?:[._]\d+){0,3}$", re.IGNORECASE)


def version_segments(url: str) -> Tuple[str, ...]:
    """Version-looking path segments of a URL, lowercased (may be empty)."""
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return ()
    return tuple(seg.lower() for seg in path.split("/") if seg and _VERSION_SEG_RE.match(seg))


def title_similarity(a: str, b: str) -> float:
    ta = set(t.lower() for t in _TOKEN_RE.findall(a or ""))
    tb = set(t.lower() for t in _TOKEN_RE.findall(b or ""))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Group:
    key: str
    hits: List[SearchHit] = field(default_factory=list)
    merged_urls: List[str] = field(default_factory=list)


def dedup(hits: List[SearchHit], threshold: float) -> Tuple[List[Group], List[Dict[str, Any]]]:
    log: List[Dict[str, Any]] = []
    groups: Dict[str, Group] = {}
    order: List[str] = []
    for hit in hits:
        key = canonical_url(hit.url)
        if key is None:
            continue  # non-web URLs never reach ranking
        if key in groups:
            group = groups[key]
            group.hits.append(hit)
            if hit.url not in group.merged_urls and hit.url != group.hits[0].url:
                group.merged_urls.append(hit.url)
            log.append({"action": "merge", "reason": "canonical_url", "kept": group.hits[0].url,
                        "merged": hit.url, "engine": hit.engine})
        else:
            groups[key] = Group(key=key, hits=[hit])
            order.append(key)

    # Pass 2: near-duplicate titles on the same registrable host.
    survivors: List[Group] = []
    for key in order:
        group = groups[key]
        target: Optional[Group] = None
        group_versions = version_segments(group.hits[0].url)
        for kept in survivors:
            if registrable_host(kept.hits[0].url) != registrable_host(group.hits[0].url):
                continue
            # Distinctly versioned pages (…/3.10/ vs …/3.12/, /v1 vs /v2) are
            # different pages even when their titles are nearly identical.
            kept_versions = version_segments(kept.hits[0].url)
            if kept_versions != group_versions and (kept_versions or group_versions):
                continue
            sim = title_similarity(kept.hits[0].title, group.hits[0].title)
            if sim >= threshold:
                target = kept
                log.append({"action": "merge", "reason": "near_duplicate",
                            "kept": kept.hits[0].url, "merged": group.hits[0].url,
                            "similarity": round(sim, 3)})
                break
        if target is None:
            survivors.append(group)
        else:
            target.hits.extend(group.hits)
            target.merged_urls.append(group.hits[0].url)
    return survivors, log
