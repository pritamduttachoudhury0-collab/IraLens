# -*- coding: utf-8 -*-
"""Typed schemas for research output.

`ResearchReport` is the public contract of `IraLens.research()`. Every
claim cites a source, every source cites the queries that found it, and the
trace is enough to replay the run deterministically (see planner.replay).
All content is untrusted web text, and the report says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..search.schema import FilterError


@dataclass(frozen=True)
class ResearchOptions:
    max_rounds: int = 3
    max_queries: int = 12
    min_sources: int = 3
    max_results: int = 8
    read_top_n: int = 3

    def __post_init__(self) -> None:
        bounds = {"max_rounds": (1, 10), "max_queries": (1, 50), "min_sources": (1, 20),
                  "max_results": (1, 50), "read_top_n": (0, 20)}
        for name, (lo, hi) in bounds.items():
            value = getattr(self, name)
            if not lo <= value <= hi:
                raise FilterError(f"{name} must be between {lo} and {hi}, got {value}")

    @classmethod
    def build(cls, data: Optional[Dict[str, Any]] = None, **defaults: Any) -> "ResearchOptions":
        merged = {**defaults, **{k: v for k, v in (data or {}).items() if v is not None}}
        allowed = set(cls.__dataclass_fields__)
        unknown = set(merged) - allowed
        if unknown:
            raise FilterError(f"unknown research option(s): {sorted(unknown)}; allowed: {sorted(allowed)}")
        return cls(**merged)


@dataclass
class EvidenceSource:
    id: str
    url: str
    canonical_url: str
    title: str
    domain: str
    snippet: str = ""
    content: str = ""                    # page text when read, else empty
    published_at: Optional[str] = None
    engines: List[str] = field(default_factory=list)
    rounds: List[int] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    security_flags: List[str] = field(default_factory=list)
    read_status: str = "snippet_only"    # read | read_failed | snippet_only

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("content", None)        # page text lives in the trace, not the summary
        return data


@dataclass
class Claim:
    id: str
    text: str
    source_id: str
    subject_terms: List[str]
    numbers: List[str]
    negated: bool
    security_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Contradiction:
    id: str
    kind: str                            # numeric_mismatch | negation_mismatch
    claim_ids: List[str]
    subject_terms: List[str]
    explanation: str
    confidence: str = "heuristic"        # always heuristic: this is a candidate, not a verdict

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Statement:
    id: str
    text: str
    status: str                          # corroborated | single_source | contested
    confidence: float
    claim_ids: List[str]
    source_ids: List[str]
    distinct_domains: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchReport:
    question: str
    stop_reason: str
    rounds_run: int
    coverage: float
    queries: List[str]
    sources: List[Dict[str, Any]]
    claims: List[Dict[str, Any]]
    contradictions: List[Dict[str, Any]]
    statements: List[Dict[str, Any]]
    limitations: List[str]
    security_flags: List[str]
    provenance: Dict[str, Any]
    options: Dict[str, Any]
    trace: Dict[str, Any]
    untrusted: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def render_text(self) -> str:
        """Human-readable summary with [S#] citations that map to the source list."""
        order = {s["id"]: i + 1 for i, s in enumerate(self.sources)}
        lines = [f"Question: {self.question}", f"Stopped: {self.stop_reason} after {self.rounds_run} round(s); "
                 f"coverage {self.coverage:.2f}", "", "Findings:"]
        if not self.statements:
            lines.append("- none (see limitations)")
        for st in self.statements:
            cites = "".join(f"[S{order[s]}]" for s in st["source_ids"] if s in order)
            lines.append(f"- ({st['status']}, confidence {st['confidence']:.2f}) {st['text']} {cites}")
        if self.contradictions:
            lines += ["", "Possible contradictions (heuristic, verify before relying on them):"]
            for c in self.contradictions:
                lines.append(f"- {c['kind']}: {c['explanation']}")
        lines += ["", "Sources:"]
        for s in self.sources:
            lines.append(f"[S{order[s['id']]}] {s['title']} — {s['url']} ({s['read_status']})")
        if self.limitations:
            lines += ["", "Limitations:"] + [f"- {x}" for x in self.limitations]
        return "\n".join(lines)
