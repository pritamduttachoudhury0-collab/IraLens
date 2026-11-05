# -*- coding: utf-8 -*-
"""Curated publisher feed registry (the "source pack").

A general search engine answers "what does the web say". A curated pack answers
"what do the publishers who actually cover this beat say" — which is how local
and regional news becomes reachable at all. A flood in Assam is covered by the
Assam Tribune and EastMojo long before (or instead of) a global wire, and those
outlets are invisible to a US-centric search index.

The pack is plain data: publisher name, feed URL, domain, region, language,
beats, and a credibility tier. It is *not* a filter that hides other sources —
it is an additional discovery lane that runs alongside the news feeds and the
general engines.

Region codes use ISO country codes with optional subdivision suffixes
(``IN-AS`` = Assam, ``IN-NE`` = India's Northeast). A query for region ``IN``
matches ``IN``, ``IN-AS`` and ``IN-NE``.

Feed data adapted from PyNewsBrowser (MIT, (c) 2026 PyNewsBrowser contributors).
Publisher feeds are public RSS intended for feed readers; respect each
publisher's terms and the shared per-host rate limiter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class PublisherFeed:
    name: str
    url: str
    domain: str
    region: str
    language: str
    topics: Tuple[str, ...]
    tier: int

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "url": self.url, "domain": self.domain,
                "region": self.region, "language": self.language,
                "topics": list(self.topics), "tier": self.tier}


def _f(name: str, url: str, domain: str, region: str, language: str,
       topics: str, tier: int) -> PublisherFeed:
    return PublisherFeed(name=name, url=url, domain=domain, region=region,
                         language=language, topics=tuple(topics.split(",")), tier=tier)


#: The curated pack. Ordered roughly by breadth, then region, then beat.
PUBLISHER_FEEDS: Tuple[PublisherFeed, ...] = (
    _f("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "bbc.com", "GB", "en", "world,politics,crisis", 1),
    _f("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "bbc.com", "GB", "en", "technology,ai", 1),
    _f("The Guardian World", "https://www.theguardian.com/world/rss", "theguardian.com", "GB", "en", "world,politics", 1),
    _f("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "aljazeera.com", "QA", "en", "world,crisis,politics", 1),
    _f("NPR News", "https://feeds.npr.org/1001/rss.xml", "npr.org", "US", "en", "us,politics", 1),
    _f("DW News", "https://rss.dw.com/rdf/rss-en-all", "dw.com", "DE", "en", "world,politics", 1),
    _f("France 24", "https://www.france24.com/en/rss", "france24.com", "FR", "en", "world", 1),
    _f("The Hindu National", "https://www.thehindu.com/news/national/feeder/default.rss", "thehindu.com", "IN", "en", "india,politics", 1),
    _f("The Hindu International", "https://www.thehindu.com/news/international/feeder/default.rss", "thehindu.com", "IN", "en", "world", 1),
    _f("Indian Express India", "https://indianexpress.com/section/india/feed/", "indianexpress.com", "IN", "en", "india,politics", 1),
    _f("Indian Express Technology", "https://indianexpress.com/section/technology/feed/", "indianexpress.com", "IN", "en", "technology,ai", 1),
    _f("Times of India India", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms", "timesofindia.indiatimes.com", "IN", "en", "india", 2),
    _f("The Assam Tribune", "https://assamtribune.com/feed", "assamtribune.com", "IN-AS", "en", "assam,northeast,local", 2),
    _f("The Sentinel Assam", "https://www.sentinelassam.com/feed", "sentinelassam.com", "IN-AS", "en", "assam,northeast,local", 2),
    _f("EastMojo", "https://www.eastmojo.com/feed/", "eastmojo.com", "IN-NE", "en", "northeast,local", 2),
    _f("Northeast Today", "https://northeasttoday.in/feed/", "northeasttoday.in", "IN-NE", "en", "northeast,local", 2),
    _f("MIT Technology Review", "https://www.technologyreview.com/feed/", "technologyreview.com", "US", "en", "technology,ai", 1),
    _f("TechCrunch", "https://techcrunch.com/feed/", "techcrunch.com", "US", "en", "technology,ai,startups", 1),
    _f("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", "venturebeat.com", "US", "en", "ai,technology", 2),
    _f("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "arstechnica.com", "US", "en", "technology,science", 1),
    _f("The Verge", "https://www.theverge.com/rss/index.xml", "theverge.com", "US", "en", "technology,ai", 1),
    _f("Politico", "https://www.politico.com/rss/politicopicks.xml", "politico.com", "US", "en", "politics,crisis", 1),
)

#: Words in a query that select a beat. Kept small and explicit: this biases
#: which feeds are read, it never restricts what the other lanes may return.
_TOPIC_HINTS: Dict[str, Tuple[str, ...]] = {
    "technology": ("technology", "tech", "software", "gadget", "chip", "semiconductor", "startup"),
    "ai": ("ai", "artificial intelligence", "llm", "openai", "machine learning", "chatbot"),
    "politics": ("politics", "political", "election", "government", "parliament", "minister", "policy", "crisis"),
    "crisis": ("crisis", "war", "conflict", "protest", "coup", "disaster", "earthquake", "flood", "outbreak", "attack"),
    "science": ("science", "research", "space", "nasa", "climate", "physics", "biology"),
    "business": ("business", "economy", "earnings", "market", "inflation", "interest rate", "fed", "reserve bank"),
    "local": ("local", "city", "district", "state", "assam", "guwahati", "northeast", "northeastern"),
    "india": ("india", "indian", "assam", "guwahati", "northeast", "delhi", "mumbai"),
}

#: Query words that imply a region, so a local question reaches local outlets
#: even when the caller passed no explicit region filter.
_REGION_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("IN-AS", ("assam", "guwahati", "dibrugarh", "jorhat", "silchar", "tezpur")),
    ("IN-NE", ("northeast", "north east", "meghalaya", "shillong", "manipur", "imphal",
               "nagaland", "kohima", "mizoram", "aizawl", "tripura", "agartala", "arunachal")),
    ("IN", ("india", "indian", "delhi", "mumbai", "bengaluru", "chennai", "kolkata", "rbi")),
    ("GB", ("britain", "british", "uk", "united kingdom", "london", "westminster")),
    ("US", ("united states", "usa", "america", "washington", "congress", "white house", "federal reserve")),
)


def detect_region(question: str) -> Optional[str]:
    """The region a question implies, or None. Deterministic and offline."""
    low = " ".join((question or "").lower().split())
    if not low:
        return None
    for region, words in _REGION_HINTS:
        for word in words:
            if f" {word} " in f" {low} ":
                return region
    return None


def detect_topics(question: str) -> Tuple[str, ...]:
    """Beats a question touches, in a stable order."""
    low = " ".join((question or "").lower().split())
    if not low:
        return ()
    found: List[str] = []
    for topic, words in _TOPIC_HINTS.items():
        if any(f" {w} " in f" {low} " for w in words):
            found.append(topic)
    return tuple(found)


def region_matches(feed_region: str, wanted: Optional[str]) -> bool:
    """``IN`` matches ``IN``, ``IN-AS`` and ``IN-NE``; ``IN-AS`` matches only itself."""
    if not wanted:
        return True
    wanted = wanted.upper()
    feed_region = feed_region.upper()
    return feed_region == wanted or feed_region.startswith(wanted + "-")


def select_feeds(question: str = "", *, region: Optional[str] = None,
                 language: Optional[str] = None, limit: int = 6,
                 domains: Sequence[str] = ()) -> List[PublisherFeed]:
    """Pick the feeds most likely to cover this question.

    Scoring is deliberately simple and explainable: an explicit domain filter
    wins outright; then region match, then beat overlap, then credibility tier.
    The result is bounded so one query never fans out to the whole pack.
    """
    wanted_region = region or detect_region(question)
    topics = set(detect_topics(question))
    wanted_domains = {d.lower().removeprefix("www.") for d in domains if d}
    scored: List[Tuple[float, int, PublisherFeed]] = []
    for index, feed in enumerate(PUBLISHER_FEEDS):
        if wanted_domains and feed.domain not in wanted_domains:
            continue
        if language and feed.language != language.lower():
            continue
        score = 0.0
        if wanted_domains:
            score += 10.0
        if wanted_region:
            if feed.region.upper() == wanted_region.upper():
                score += 4.0
            elif region_matches(feed.region, wanted_region):
                score += 2.5
        overlap = topics & set(feed.topics)
        score += 1.5 * len(overlap)
        score += 0.5 if feed.tier == 1 else 0.0
        # A region-specific question should not be answered only by global
        # wires; a small penalty keeps the pack diverse when nothing matched.
        if not wanted_region and not overlap:
            score += 0.1
        scored.append((-score, index, feed))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [feed for _, _, feed in scored[:max(1, limit)]]


def archive_domains(limit: int = 3, *, region: Optional[str] = None) -> List[str]:
    """Distinct publisher domains for the sitemap/archive lane.

    Deduplicated on purpose: several curated feeds belong to one publisher
    (BBC World + BBC Technology), and taking the first N *feeds* would spend
    the whole domain budget on a single site.
    """
    out: List[str] = []
    for feed in PUBLISHER_FEEDS:
        if feed.tier != 1:
            continue
        if region and not region_matches(feed.region, region):
            continue
        if feed.domain not in out:
            out.append(feed.domain)
        if len(out) >= max(1, limit):
            break
    if not out:  # an unknown region still deserves a bounded global try
        return archive_domains(limit=limit)
    return out


__all__ = ["PublisherFeed", "PUBLISHER_FEEDS", "select_feeds", "archive_domains",
           "detect_region", "detect_topics", "region_matches"]
