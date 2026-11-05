# -*- coding: utf-8 -*-
"""Story clustering, event timelines and trend signals over search results.

A list of ten links does not answer "what is happening with the Assam floods".
The same wire story appears on eight sites, three genuinely different
developments appear once each, and the reader cannot tell which is which.
This module turns a ranked result list into:

* **clusters** — one group per story, so eight copies of one wire report count
  as one story reported by eight outlets, not eight stories;
* an **event timeline** — first report, latest update, how many distinct
  publishers are covering it;
* **trend signals** — which stories are being picked up fastest by the most
  independent publishers.

Honesty rules:
* Only *publication* dates enter a timeline. A revision date or an unknown date
  never becomes "first report" or "latest update".
* A trend score is a transparent heuristic over counts and recency. It is not a
  probability, not an official "trending" list, and every signal carries the
  counts it was computed from so a caller can check it.
* Nothing here invents a story, a date or a publisher. Every cluster member is
  a real result that came back from a real provider.

Clustering approach adapted from PyNewsBrowser (MIT, (c) 2026 PyNewsBrowser
contributors), rewritten over IraLens's ranked results and syndication keys.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .news import looks_syndicated, source_quality
from .urls import canonical_url, domain_of

#: Two reports of one event are usually within days of each other. Beyond this
#: gap, near-identical titles are more likely a recurring/evergreen page than
#: the same story.
CLUSTER_TIME_GAP_DAYS = 10
#: Title similarity at which two results are treated as one story.
CLUSTER_TITLE_THRESHOLD = 0.62
#: Compare a new result against the first few members of a group, not just its
#: representative, so grouping does not depend on which copy scored highest.
CLUSTER_COMPARE_MEMBERS = 3
_TITLE_TOKEN_RE = re.compile(r"[\w\u00c0-\u024f\u0900-\u097f]+", re.UNICODE)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _age_days(value: str, today: date) -> Optional[float]:
    try:
        return float((today - date.fromisoformat(value[:10])).days)
    except (ValueError, TypeError):
        return None


@dataclass
class StoryCluster:
    """One story, possibly reported by several publishers."""

    id: str
    title: str
    urls: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    first_report: Optional[str] = None
    latest_update: Optional[str] = None
    story_count: int = 0
    source_count: int = 0
    score: float = 0.0
    #: How many members carry a real publication date (the rest are undated).
    dated_count: int = 0
    primary_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "urls": list(self.urls),
            "sources": list(self.sources), "first_report": self.first_report,
            "latest_update": self.latest_update, "story_count": self.story_count,
            "source_count": self.source_count, "score": self.score,
            "dated_count": self.dated_count, "primary_url": self.primary_url,
        }


@dataclass
class EventTimeline:
    """Coverage of one developing event, ordered by real publication dates."""

    question: str
    clusters: List[StoryCluster] = field(default_factory=list)
    first_report: Optional[str] = None
    latest_update: Optional[str] = None
    source_count: int = 0
    story_count: int = 0
    dated_story_count: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "clusters": [c.to_dict() for c in self.clusters],
            "first_report": self.first_report, "latest_update": self.latest_update,
            "source_count": self.source_count, "story_count": self.story_count,
            "dated_story_count": self.dated_story_count, "note": self.note,
        }


@dataclass
class TrendSignal:
    """A story gaining coverage. `score` is a heuristic, not a probability."""

    topic: str
    score: float
    window: str
    source_count: int = 0
    story_count: int = 0
    evidence: List[str] = field(default_factory=list)
    representative_urls: List[str] = field(default_factory=list)
    latest_update: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic, "score": self.score, "window": self.window,
            "source_count": self.source_count, "story_count": self.story_count,
            "evidence": list(self.evidence),
            "representative_urls": list(self.representative_urls),
            "latest_update": self.latest_update,
        }


#: Pure function words carry no story identity. Content words — including
#: numbers, "over", "under", "not" — are deliberately kept, because "over 100
#: dead" and "100 dead" are different claims.
_STORY_STOPWORDS = frozenset("""
a an the and or of in on at to for by with from as is are was were be been
it its this that has have had will would can could
""".split())


def _story_stem(token: str) -> str:
    """Fold common English inflections so two headlines about one event match.

    Headline writers say "floods displace thousands" where another outlet says
    "thousands displaced" — the same story, but raw token overlap misses it.
    This is a deliberately tiny, dependency-free fold, not a full stemmer: it
    only has to make inflected forms of the same word compare equal. It is
    applied to *both* sides, so over-folding is symmetric and harmless, while
    genuinely different words ("holds" vs "cuts") stay different.
    """
    t = token
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 4 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 4 and t.endswith("ed"):
        t = t[:-2]
    elif len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    if len(t) > 3 and t.endswith("e"):
        t = t[:-1]
    return t


def _story_tokens(title: str) -> set:
    return {_story_stem(t.lower()) for t in _TITLE_TOKEN_RE.findall(title or "")
            if t.lower() not in _STORY_STOPWORDS}


def _story_similarity(a: str, b: str) -> float:
    """Jaccard similarity of two headlines as *stories*, not as strings.

    Kept an honest measure: 1.0 means the same set of story words, not merely
    that one headline contains the other.
    """
    ta, tb = _story_tokens(a), _story_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _story_containment(a: str, b: str) -> float:
    """How much of the shorter headline is contained in the longer one.

    An outlet that adds a clause to a wire headline should not split the story.
    The 4-token floor keeps this from merging very short, generic headlines.
    """
    ta, tb = _story_tokens(a), _story_tokens(b)
    shorter = min(len(ta), len(tb))
    if shorter < 4:
        return 0.0
    return len(ta & tb) / shorter


def _same_story(a, b, *, threshold: float = CLUSTER_TITLE_THRESHOLD,
                today: Optional[date] = None) -> bool:
    """Whether two ranked results are reports of one story.

    ``today`` is injectable so clustering is reproducible in tests and in a
    replayed query, instead of drifting with the wall clock."""
    if canonical_url(a.url) and canonical_url(a.url) == canonical_url(b.url):
        return True
    # A wire copy carried by two outlets has near-identical wording once the
    # outlet boilerplate is stripped. This shortcut keeps its own conservative
    # 0.8 floor, but must still tighten when the caller asks for a stricter
    # threshold — otherwise `threshold` is silently ignored for wire copies.
    if looks_syndicated(a.title, b.title, threshold=max(0.8, threshold)):
        return True
    similar = _story_similarity(a.title, b.title) >= threshold
    # The containment rule is a default-strength heuristic, so a caller asking
    # for a stricter threshold gets strict similarity only.
    contained = (threshold <= CLUSTER_TITLE_THRESHOLD
                 and _story_containment(a.title, b.title) >= 0.85)
    if not (similar or contained):
        return False
    if a.published_at and b.published_at:
        now = today or _today()
        gap_a, gap_b = _age_days(a.published_at, now), _age_days(b.published_at, now)
        if gap_a is not None and gap_b is not None:
            return abs(gap_a - gap_b) <= CLUSTER_TIME_GAP_DAYS
    return True


def cluster_results(results: Sequence[Any], *,
                    threshold: float = CLUSTER_TITLE_THRESHOLD,
                    today: Optional[date] = None) -> List[StoryCluster]:
    """Group ranked results into stories. Pure and deterministic."""
    clusters: List[StoryCluster] = []
    members: List[List[Any]] = []
    ordered = sorted(results, key=lambda r: (-getattr(r, "score", 0.0), r.url))
    for result in ordered:
        placed = False
        for group in members:
            if any(_same_story(result, member, threshold=threshold, today=today)
                   for member in group[:CLUSTER_COMPARE_MEMBERS]):
                group.append(result)
                placed = True
                break
        if not placed:
            members.append([result])
    for group in members:
        rep = group[0]
        dates = sorted(r.published_at for r in group if r.published_at)
        domains = sorted({domain_of(r.url) for r in group if domain_of(r.url)})
        # Prefer the most credible publisher's copy as the story's canonical
        # link, so the reader lands on the original rather than a reprint.
        primary = max(group, key=lambda r: (source_quality(r.url), getattr(r, "score", 0.0)))
        cluster_id = hashlib.sha1((primary.url or rep.title).encode("utf-8")).hexdigest()[:12]
        best_score = max(getattr(r, "score", 0.0) for r in group)
        clusters.append(StoryCluster(
            id=cluster_id, title=rep.title or rep.url,
            urls=[r.url for r in group], sources=domains,
            first_report=dates[0] if dates else None,
            latest_update=dates[-1] if dates else None,
            story_count=len(group), source_count=len(domains),
            dated_count=len(dates),
            # Independent publishers agreeing is the strongest signal available
            # here, so it lifts a cluster above a single well-scored page.
            score=round(best_score + min(len(domains), 5) * 0.05, 4),
            primary_url=primary.url,
        ))
    clusters.sort(key=lambda c: (-c.score, c.id))
    return clusters


def build_timeline(question: str, results: Sequence[Any], *,
                   today: Optional[date] = None) -> EventTimeline:
    """An event timeline built only from real publication dates."""
    clusters = cluster_results(results, today=today)
    dated = [c for c in clusters if c.first_report]
    firsts = sorted(c.first_report for c in dated if c.first_report)
    lasts = sorted(c.latest_update for c in dated if c.latest_update)
    sources = sorted({s for c in clusters for s in c.sources})
    note = ""
    if not results:
        note = "no results to build a timeline from"
    elif not dated:
        note = ("no result carries a verifiable publication date, so no timeline "
                "order can be established")
    elif len(dated) < len(clusters):
        note = (f"{len(clusters) - len(dated)} of {len(clusters)} stories have no "
                "verifiable publication date and are excluded from the ordering")
    return EventTimeline(
        question=question, clusters=clusters,
        first_report=firsts[0] if firsts else None,
        latest_update=lasts[-1] if lasts else None,
        source_count=len(sources), story_count=len(clusters),
        dated_story_count=len(dated), note=note,
    )


def detect_trends(question: str, results: Sequence[Any], *,
                  window_days: int = 2, limit: int = 8,
                  volume_series: Optional[Sequence[Dict[str, Any]]] = None,
                  today: Optional[date] = None) -> List[TrendSignal]:
    """Which stories are gaining coverage, with the counts behind each score.

    Two independent signals, both explainable:
    * **cluster velocity** — how many distinct publishers picked a story up,
      and how recently. A story on six outlets in 48 hours is spreading;
      the same story on one outlet is not.
    * **coverage volume** (optional) — when a provider supplies a dated volume
      series, recent volume against its own prior baseline.

    The score is a bounded heuristic, never a calibrated probability.
    """
    now = today or _today()
    signals: List[TrendSignal] = []
    recent = []
    for result in results:
        age = _age_days(result.published_at, now) if result.published_at else None
        if age is not None and 0 <= age <= window_days:
            recent.append(result)
    # With no dated recent coverage, fall back to the full set rather than
    # pretending nothing is happening — the evidence strings say which it was.
    basis = recent or list(results)
    used_window = f"{window_days}d" if recent else "undated/all-results"
    for cluster in cluster_results(basis, today=now)[:max(1, limit)]:
        recency = 0.0
        if cluster.latest_update:
            age = _age_days(cluster.latest_update, now)
            if age is not None:
                recency = max(0.0, 1.0 - age / max(1, window_days))
        raw = cluster.source_count * 0.6 + recency * 0.4 + min(cluster.story_count, 10) * 0.05
        signals.append(TrendSignal(
            topic=cluster.title, score=round(raw, 3), window=used_window,
            source_count=cluster.source_count, story_count=cluster.story_count,
            evidence=[f"{cluster.story_count} report(s) across {cluster.source_count} "
                      f"distinct publisher(s)",
                      f"{cluster.dated_count} of {cluster.story_count} carry a publication date"],
            representative_urls=cluster.urls[:3], latest_update=cluster.latest_update,
        ))
    if volume_series:
        volume_signal = _volume_trend(question, volume_series, results, today=now)
        if volume_signal is not None:
            signals.append(volume_signal)
    signals.sort(key=lambda s: (-s.score, s.topic))
    return signals[:max(1, limit)]


def _volume_trend(question: str, series: Sequence[Dict[str, Any]],
                  results: Sequence[Any], *,
                  today: Optional[date] = None) -> Optional[TrendSignal]:
    """Recent coverage volume against its own baseline, when a provider gives it."""
    now = today or _today()
    points = []
    for point in series:
        value = point.get("value")
        when = point.get("date")
        age = _age_days(str(when), now) if when else None
        if age is None or value is None:
            continue
        try:
            points.append((age, float(value)))
        except (TypeError, ValueError):
            continue
    if not points:
        return None
    recent = sum(v for age, v in points if age <= 1)
    baseline_points = [v for age, v in points if age > 1]
    baseline = (sum(baseline_points) / len(baseline_points)) if baseline_points else 0.0
    ratio = recent / max(baseline, 1.0)
    return TrendSignal(
        topic=question or "coverage volume", score=round(ratio, 3),
        window="24h vs prior baseline", source_count=0, story_count=len(results),
        evidence=[f"provider volume recent={recent:.0f}, baseline={baseline:.2f}",
                  "volume is a coverage count, not a verified trend ranking"],
        representative_urls=[r.url for r in list(results)[:3]],
    )


__all__ = ["StoryCluster", "EventTimeline", "TrendSignal", "cluster_results",
           "build_timeline", "detect_trends", "CLUSTER_TITLE_THRESHOLD"]
