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
from typing import Any, Dict, List, Optional, Tuple

from ... import security
from ...errors import SourceUnavailableError
from ...fetch import fetch_url_text
from ...config import state_dir
from ...reliability import PersistentCircuitBreaker, classify_block, failure_kind
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
        self._persistent_health: Optional[PersistentCircuitBreaker] = None

    # ------------------------------------------------------------- rotation
    def _instances(self, settings: Settings) -> Tuple[str, ...]:
        return tuple(i.rstrip("/") for i in settings.searxng_instances if i.strip())

    @staticmethod
    def _health_key(instance: str) -> str:
        parts = urllib.parse.urlsplit(instance)
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return f"searxng:{host}:{port}"

    def _health_store(self, cooldown_seconds: float) -> PersistentCircuitBreaker:
        if self._persistent_health is None:
            # Use wall time for durable deadlines. A custom clock is retained
            # for deterministic unit tests; monotonic timestamps never hit disk.
            clock = time.time if self._clock is time.monotonic else self._clock
            self._persistent_health = PersistentCircuitBreaker(
                threshold=1,
                cooldown=cooldown_seconds,
                blocked_cooldown=cooldown_seconds,
                state_path=state_dir() / "search-health.sqlite3",
                clock=clock,
            )
        return self._persistent_health

    def _candidate_instances(self, instances: Tuple[str, ...]) -> List[str]:
        """Rotation order (round-robin), skipping in-memory and durable cooldowns."""
        if not instances:
            return []
        with self._lock:
            start = self._next_index % len(instances)
            self._next_index = (start + 1) % len(instances)
            now = self._clock()
            cooling = {k for k, until in self._cooldown_until.items() if until > now}
        order = list(instances[start:]) + list(instances[:start])
        health = self._persistent_health
        return [i for i in order if i not in cooling and
                (health is None or health.allow(self._health_key(i)))]

    def _record_failure(self, instance: str, cooldown_seconds: float,
                        kind: str = "unavailable") -> None:
        with self._lock:
            self._cooldown_until[instance] = self._clock() + cooldown_seconds
        if self._persistent_health is not None:
            self._persistent_health.record_failure(self._health_key(instance), kind=kind)

    def _record_success(self, instance: str) -> None:
        with self._lock:
            self._cooldown_until.pop(instance, None)
        if self._persistent_health is not None:
            self._persistent_health.record_success(self._health_key(instance))

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
        self._health_store(cooldown)
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
                                      headers={"Accept": "application/json"},
                                      # A self-hosted instance on the LAN or on
                                      # 127.0.0.1 must be explicitly allowlisted
                                      # (IRALENS_ALLOWED_PRIVATE_HOSTS, D-082);
                                      # this flag only consults that allowlist.
                                      allow_private_hosts=True)
            except Exception as exc:
                self._record_failure(instance, cooldown, failure_kind(exc))
                errors.append(f"{instance}: {exc}")
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                block = classify_block(text)
                self._record_failure(instance, cooldown, block or "unavailable")
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
        hint = "pin reliable instances with IRALENS_SEARXNG_INSTANCES"
        blocked_private = []
        for instance in candidates:
            parts = urllib.parse.urlsplit(instance)
            host = (parts.hostname or "").lower()
            if security.address_is_private(host) and not security.private_host_is_allowlisted(
                    host, parts.port):
                blocked_private.append(f"{host}:{parts.port or (443 if parts.scheme == 'https' else 80)}")
        if blocked_private:
            hint = ("a self-hosted instance on a private address needs the explicit "
                    f"allowlist: IRALENS_ALLOWED_PRIVATE_HOSTS={','.join(blocked_private)}; " + hint)
        raise SourceUnavailableError(
            "searxng: all configured instances failed",
            hint=hint,
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
