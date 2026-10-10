# -*- coding: utf-8 -*-
"""SearXNG backend: JSON API over SSRF-safe HTTP with instance rotation (D-078).

SearXNG metasearch instances aggregate several upstream engines behind one
JSON API, which is the cheapest way to survive a single endpoint rate-limiting
us: instances are tried in rotation and an instance that fails goes on a
per-instance cooldown (600 s default) so the chain does not hammer it.

Parsing is a pure function of the JSON payload (`parse_response`), so it is
tested offline; the network step is `fetch.fetch_url_text`. Bundled instances
are unverified conveniences — pin your own with `IRALENS_SEARXNG_INSTANCES`
(or config key `searxng_instances`) for production (KNOWN_LIMITATIONS.md).

Filter handling: language -> native (`language` param); region -> native
(`language` when it carries a region); include_domains -> emulated (`site:`);
file_type -> emulated (`filetype:`). Dates and excludes are post-filtered.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Tuple

from ...errors import SourceUnavailableError
from ...fetch import fetch_url_text
from ...reliability import classify_block
from ...search.schema import SearchFilters, SearchHit
from ...settings import Settings
from .base import EMULATED, NATIVE, POST, SearchBackend


def parse_response(payload: Any) -> List[Dict[str, str]]:
    """Normalize a SearXNG JSON document to hits (title/url/snippet/published).

    Pure function: no I/O, no state. Malformed payloads yield [].
    """
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    hits: List[Dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url.startswith(("http://", "https://")) or not title:
            continue
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        published = str(item.get("publishedDate") or item.get("published") or "").strip()
        hits.append({"title": title, "url": url, "snippet": snippet, "published": published})
    return hits


class SearxngBackend(SearchBackend):
    name = "searxng"
    handled = {"language": NATIVE, "region": NATIVE, "include_domains": EMULATED,
               "file_type": EMULATED, "date_from": POST, "date_to": POST,
               "exclude_domains": POST}

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._cooldown_until: Dict[str, float] = {}
        self._next_index = 0

    # ------------------------------------------------------------- rotation
    def _instances(self, settings: Settings) -> Tuple[str, ...]:
        return tuple(i.rstrip("/") for i in settings.searxng_instances if i.strip())

    def _candidate_instances(self, instances: Tuple[str, ...]) -> List[str]:
        """Rotation order (round-robin), skipping instances cooling down."""
        if not instances:
            return []
        with self._lock:
            start = self._next_index % len(instances)
            self._next_index = (start + 1) % len(instances)
            now = self._clock()
            cooling = {k for k, until in self._cooldown_until.items() if until > now}
        order = list(instances[start:]) + list(instances[:start])
        return [i for i in order if i not in cooling]

    def _record_failure(self, instance: str, cooldown_seconds: float) -> None:
        with self._lock:
            self._cooldown_until[instance] = self._clock() + cooldown_seconds

    def _record_success(self, instance: str) -> None:
        with self._lock:
            self._cooldown_until.pop(instance, None)

    # --------------------------------------------------------------- search
    def build_query(self, query: str, filters: SearchFilters) -> str:
        parts = [query]
        if filters.include_domains:
            parts.insert(0, f"site:{filters.include_domains[0]}")
        if filters.file_type:
            parts.append(f"filetype:{filters.file_type}")
        return " ".join(parts)

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any) -> List[SearchHit]:
        settings = Settings.from_config(getattr(context, "config", None))
        cooldown = float(settings.searxng_cooldown_seconds)
        timeout = int(settings.searxng_timeout_seconds)
        instances = self._instances(settings)
        if not instances:
            raise SourceUnavailableError(
                "searxng has no instances configured",
                hint="set IRALENS_SEARXNG_INSTANCES to a comma-separated list",
                detail="no_instances",
            )
        candidates = self._candidate_instances(instances)
        if not candidates:
            raise SourceUnavailableError(
                "searxng: every instance is cooling down after failures",
                hint="wait for the cooldown to expire or pin more instances",
                detail="all_instances_cooling_down",
            )
        errors: List[str] = []
        for instance in candidates:
            url = self._build_url(instance, query, filters, limit)
            try:
                text = fetch_url_text(url, timeout=timeout,
                                      headers={"Accept": "application/json"})
            except Exception as exc:
                self._record_failure(instance, cooldown)
                errors.append(f"{instance}: {exc}")
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                self._record_failure(instance, cooldown)
                block = classify_block(text)
                errors.append(f"{instance}: non-json response ({block or 'not json'})")
                continue
            hits = parse_response(payload)
            self._record_success(instance)
            return [
                SearchHit(title=h["title"], url=h["url"], snippet=h["snippet"][:400],
                          engine=self.name, position=i + 1,
                          published_at=h["published"] or None, query=query)
                for i, h in enumerate(hits[: limit * 2])
            ]
        raise SourceUnavailableError(
            "searxng: all configured instances failed",
            hint="pin reliable instances with IRALENS_SEARXNG_INSTANCES",
            detail="; ".join(errors)[:300],
        )

    def _build_url(self, instance: str, query: str, filters: SearchFilters, limit: int) -> str:
        params: Dict[str, str] = {
            "q": self.build_query(query, filters),
            "format": "json",
            "pageno": "1",
        }
        if filters.language:
            params["language"] = filters.language
        elif filters.region:
            params["language"] = filters.region
        if filters.date_from and filters.date_to:
            pass  # post-filtered; SearXNG has no arbitrary date range
        return f"{instance}/search?{urllib.parse.urlencode(params)}"
