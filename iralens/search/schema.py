# -*- coding: utf-8 -*-
"""Typed schemas for the unified search API.

These are plain dataclasses with `to_dict()` so they serialize to JSON for the
CLI and MCP without extra dependencies. They are the public contract of
`IraLens.search_api()`. The legacy `search()` still returns `Artifact`s.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")


class FilterError(ValueError):
    """Raised for an invalid filter. The message says what to change."""


@dataclass(frozen=True)
class SearchFilters:
    date_from: Optional[str] = None          # ISO yyyy-mm-dd, inclusive
    date_to: Optional[str] = None            # ISO yyyy-mm-dd, inclusive
    include_domains: tuple = ()
    exclude_domains: tuple = ()
    file_type: Optional[str] = None          # e.g. "pdf"
    language: Optional[str] = None           # BCP-47-ish, e.g. "en"
    region: Optional[str] = None             # e.g. "in-en"

    @classmethod
    def build(
        cls,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        include_domains: Sequence[str] = (),
        exclude_domains: Sequence[str] = (),
        file_type: Optional[str] = None,
        language: Optional[str] = None,
        region: Optional[str] = None,
    ) -> "SearchFilters":
        """Validate and normalize user input. Raises FilterError on bad input."""
        for label, value in (("date_from", date_from), ("date_to", date_to)):
            if value is not None and not _DATE_RE.match(value):
                raise FilterError(f"{label} must be yyyy-mm-dd, got {value!r}")
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise FilterError(f"{label} is not a real date: {value!r}") from exc
        if date_from and date_to and date_from > date_to:
            raise FilterError("date_from must not be after date_to")
        ext = None
        if file_type:
            ext = file_type.strip().lower().lstrip(".")
            if not _EXT_RE.match(ext):
                raise FilterError(f"file_type must be a bare extension like 'pdf', got {file_type!r}")
        return cls(
            date_from=date_from or None,
            date_to=date_to or None,
            include_domains=tuple(sorted({normalize_domain(d) for d in include_domains if d})),
            exclude_domains=tuple(sorted({normalize_domain(d) for d in exclude_domains if d})),
            file_type=ext,
            language=(language or None),
            region=(region or None),
        )

    def is_empty(self) -> bool:
        return not any((self.date_from, self.date_to, self.include_domains,
                        self.exclude_domains, self.file_type, self.language, self.region))

    def to_dict(self) -> Dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()}


def normalize_domain(value: str) -> str:
    """'https://www.Example.com/x' -> 'example.com'."""
    text = value.strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/")[0].split("?")[0]
    if text.startswith("www."):
        text = text[4:]
    return text


@dataclass(frozen=True)
class SearchOptions:
    max_results: int = 8
    engines: tuple = ()          # empty = configured chain
    reformulate: bool = True
    cache: str = "use"           # use | bypass | refresh

    def __post_init__(self) -> None:
        if self.cache not in ("use", "bypass", "refresh"):
            raise FilterError("cache must be one of: use, bypass, refresh")
        if not 1 <= self.max_results <= 50:
            raise FilterError("max_results must be between 1 and 50")


@dataclass
class SearchHit:
    """One normalized hit from one engine (before dedup and ranking)."""

    title: str
    url: str
    snippet: str
    engine: str
    position: int                       # 1-based rank within the engine's page
    published_at: Optional[str] = None  # ISO date when the engine reports one
    query: str = ""                     # the expanded query that produced it
    security_flags: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)  # engine-specific, kept in provenance only


@dataclass
class RankedResult:
    title: str
    url: str                            # first URL seen, unmodified
    canonical_url: str                  # dedup key (tracking params stripped)
    snippet: str
    score: float
    score_breakdown: Dict[str, float]
    engines: List[str]
    engine_ranks: Dict[str, int]
    published_at: Optional[str]
    date_status: str                    # known | unknown
    queries: List[str]
    security_flags: List[str]
    merged_from: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EngineOutcome:
    engine: str
    status: str                         # results | empty | failed | skipped
    kind: str                           # ok | empty | captcha | rate_limited | layout_changed | transient | unavailable
    count: int = 0
    detail: str = ""
    attempts: int = 1
    filter_modes: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResponse:
    query: str
    results: List[RankedResult]
    queries: List[Dict[str, str]]       # [{text, strategy}] after reformulation
    outcomes: List[EngineOutcome]
    fallbacks: List[Dict[str, str]]     # [{from, to, reason}]
    filters: Dict[str, Any]
    cache: Dict[str, Any]
    dedup_log: List[Dict[str, Any]]
    no_results_reason: str              # none | no_results | filtered_out | engines_failed
    security_flags: List[str] = field(default_factory=list)
    summary: str = ""                   # one honest sentence about the outcome

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SearchResponse":
        return cls(
            query=data["query"],
            results=[RankedResult(**r) for r in data["results"]],
            queries=data["queries"],
            outcomes=[EngineOutcome(**o) for o in data["outcomes"]],
            fallbacks=data["fallbacks"],
            filters=data["filters"],
            cache=data["cache"],
            dedup_log=data["dedup_log"],
            no_results_reason=data["no_results_reason"],
            security_flags=data.get("security_flags", []),
            summary=data.get("summary", ""),
        )

    @property
    def degraded(self) -> bool:
        return any(o.status in ("failed", "skipped") for o in self.outcomes)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["degraded"] = self.degraded
        return data
