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
    #: Drop results whose publication date is unknown. Off by default (a
    #: date filter that silently keeps undated results cannot enforce a
    #: recency window), but available when a window must be strict.
    exclude_unknown_dates: bool = False

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
        exclude_unknown_dates: bool = False,
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
            exclude_unknown_dates=bool(exclude_unknown_dates),
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
    #: How many results to skip. This is the pagination primitive: with no
    #: offset every page of a query is byte-identical, so a corpus can
    #: never grow past the first page.
    offset: int = 0
    #: URLs the caller has already collected. They are removed from the
    #: result set, which is how a multi-query sweep avoids re-returning
    #: (and re-paying for) the same pages.
    exclude_urls: tuple = ()

    def __post_init__(self) -> None:
        if self.cache not in ("use", "bypass", "refresh"):
            raise FilterError("cache must be one of: use, bypass, refresh")
        if not 1 <= self.max_results <= 50:
            raise FilterError("max_results must be between 1 and 50")
        if self.offset < 0:
            raise FilterError("offset must not be negative")
        if self.offset > 10000:
            raise FilterError("offset must not exceed 10000")


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
    #: A *different* kind of date: "the page was last changed then". Kept
    #: apart from `published_at` because a page edited today is not a page
    #: published today (see the Wikipedia revision timestamp).
    modified_at: Optional[str] = None
    #: What the date means: "publication" | "revision" | "" (unknown).
    date_kind: str = ""


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
    date_evidence: str = ""             # D7: which pattern produced published_at
    #: Last-modified time, when the source reports one and it is not a
    #: publication date. Never conflated with `published_at`.
    modified_at: Optional[str] = None
    #: "publication" | "revision" | "" — what `published_at` means.
    date_kind: str = ""
    #: True when freshness was held at neutral because relevance failed the
    #: gate. Reported so a capped score is explainable rather than mysterious.
    freshness_gated: bool = False

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
    # D6: why fewer than max_results came back ("" when full):
    # exhausted | deduped | capped | filtered
    shortfall_reason: str = ""
    # Per-engine readiness: which configured engines can serve right now, and
    # why not. A configured-but-not-installed engine is *not* a failure and
    # must not be reported as one.
    engine_readiness: Dict[str, Any] = field(default_factory=dict)
    # How much of the question the results actually address.
    coverage: Dict[str, Any] = field(default_factory=dict)
    # Whether a configured paid key was actually attempted, and the free-only
    # coverage/trigger that led to it. An absent key is never implied to be a
    # successful replacement; callers can measure the free result separately.
    paid_fallback: Dict[str, Any] = field(default_factory=dict)

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
            shortfall_reason=data.get("shortfall_reason", ""),
            engine_readiness=data.get("engine_readiness", {}),
            coverage=data.get("coverage", {}),
            paid_fallback=data.get("paid_fallback", {}),
        )

    @property
    def degraded(self) -> bool:
        return any(o.status in ("failed", "skipped") for o in self.outcomes)

    @property
    def degradation_detail(self) -> Dict[str, Any]:
        """D4: per-engine degradation, explicit — a dead first engine must be
        impossible to misread as a healthy `status=ok` (benchmark §54.2)."""
        return {
            "degraded": self.degraded,
            "answered": sorted({o.engine for o in self.outcomes if o.status in ("results", "empty")}),
            "failed": sorted({o.engine for o in self.outcomes if o.status == "failed"}),
            "skipped": sorted({o.engine for o in self.outcomes if o.status == "skipped"}),
            "skip_reasons": {o.engine: o.kind for o in self.outcomes if o.status == "skipped"},
            "shortfall_reason": self.shortfall_reason,
        }

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["degraded"] = self.degraded
        data["degradation"] = self.degradation_detail
        return data
