# -*- coding: utf-8 -*-
"""News intelligence: time-window detection, publication-date verification,
source quality, and syndication handling.

This module is the deterministic brain behind recent-news retrieval. It does
no network I/O itself; it supplies the search orchestrator and the news
backends with:

* ``detect_news_intent`` — is this a recent/breaking-news question, and what
  explicit time window does it name (today, yesterday, last 24 hours, last
  seven days, this month, ...)? The window becomes a real ``date_from`` /
  ``date_to`` constraint instead of being ignored.
* ``extract_article_dates`` — read a publisher page's *own* metadata
  (JSON-LD ``datePublished`` / ``dateModified``, OpenGraph / article meta
  tags, ``<time datetime>``) and report publication and modification times
  *separately*, with the evidence that produced each. An index-seen time or a
  retrieval time is never promoted to a publication date.
* ``normalize_timestamp`` — turn the many wire formats (RFC 822, ISO 8601
  with/without zone, ``YYYYMMDDTHHMMSSZ``) into a UTC ISO date, or ``None``.
* ``source_quality`` / ``is_primary_publisher`` — bounded credibility signals
  used by ranking, never a hard filter.
* ``syndication_key`` — a normalized title key so the same wire story carried
  by ten outlets can be deduplicated across domains.

Design rule (the mission's core honesty requirement): publication date,
updated date, index-discovery date, and retrieval date are four different
things. This module keeps them apart and marks an unknown date as unknown
rather than inventing one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from .querytext import term_set

# --------------------------------------------------------------------------- #
# Time-window detection
# --------------------------------------------------------------------------- #

#: Words that mark a question as asking for recent / breaking information.
_NEWS_MARKERS = re.compile(
    r"\b(latest|recent|today|tonight|breaking|current|currently|news|newest|"
    r"this week|this month|this morning|yesterday|just announced|announced|"
    r"update[sd]?|developing|live|right now|as of|forecast|polls?|earnings|"
    r"layoffs|outbreak|evacuation|launch schedule|order backlog|election|"
    r"summit|reporting|alerts?|monetary policy|production decision)\b",
    re.IGNORECASE,
)

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60,
    "ninety": 90,
}

#: Default window for a news question that names no explicit span. It is a
#: ranking/verification hint, not a hard filter: a "latest" question can still
#: be answered by a slightly older authoritative page when nothing newer is
#: verifiable, and the response says so.
DEFAULT_NEWS_WINDOW_DAYS = 30


@dataclass(frozen=True)
class NewsIntent:
    """The news reading of a query, with an optional explicit time window."""

    is_news: bool = False
    #: Named window in days (None when the query is news-shaped but names no
    #: span; ``DEFAULT_NEWS_WINDOW_DAYS`` is then used as a soft hint).
    window_days: Optional[int] = None
    #: Inclusive lower bound (ISO date) derived from the window, when known.
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    #: Which phrase produced the window, for diagnostics.
    window_evidence: str = ""
    #: True when the query names an explicit span (today / last 7 days ...).
    explicit_window: bool = False
    #: True when the question asks for *older* coverage ("in 2019", "archive",
    #: "between 2019 and 2021"). An archive question is still news, but recency
    #: is the wrong ranking signal for it: the date window does the work, and
    #: freshness must not penalise a 2019 article for being from 2019.
    is_archive: bool = False

    def effective_window_days(self) -> int:
        return self.window_days if self.window_days is not None else DEFAULT_NEWS_WINDOW_DAYS


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


#: Words that ask for older coverage rather than the latest story.
_ARCHIVE_MARKERS = re.compile(
    r"\b(historical|history|archive[sd]?|back in|at the time|years ago|"
    r"retrospective|looking back)\b", re.IGNORECASE)

#: Nouns that mean "journalism about a topic" rather than the topic itself.
_ARCHIVE_REPORTING = re.compile(
    r"\b(news|reporting|reports?|coverage|articles?|headlines?|stories|story|"
    r"press|journalism|newspapers?|dispatches)\b", re.IGNORECASE)
#: A recency request overrides archive wording.
_RECENCY_WORDS = re.compile(
    r"\b(latest|recent|recently|breaking|today|tonight|currently|right now|"
    r"this week|this month|yesterday)\b", re.IGNORECASE)

_YEAR = r"(?:19|20)\d{2}"
_DATEISH = rf"{_YEAR}(?:-\d{{2}}(?:-\d{{2}})?)?"


def _iso_bounds(value: str, *, end: bool = False) -> Optional[str]:
    """Turn '2019', '2019-03' or '2019-03-05' into an inclusive ISO bound."""
    parts = value.split("-")
    try:
        year = int(parts[0])
        if len(parts) == 1:
            return f"{year:04d}-12-31" if end else f"{year:04d}-01-01"
        month = int(parts[1])
        if not 1 <= month <= 12:
            return None
        if len(parts) == 2:
            if end:
                # Last day of that month, without importing calendar.
                nxt = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
                return (nxt - timedelta(days=1)).isoformat()
            return f"{year:04d}-{month:02d}-01"
        day = int(parts[2])
        return date(year, month, day).isoformat()
    except (ValueError, IndexError):
        return None


def _detect_archive_range(low: str, now: date) -> Tuple[Optional[str], Optional[str], str]:
    """An explicit older date range named in the query, as inclusive ISO bounds.

    Understands "between 2019 and 2021", "from 2019-01 to 2019-06",
    "since 2018-03" and "in 2019". A span that ends in the current year is not
    treated as archive — that is a this-year question, not a historical one.
    Returns (None, None, "") when no older span is named.
    """
    span = re.search(rf"\b(?:between|from)\s+({_DATEISH})\s+(?:and|to|until|through)\s+({_DATEISH})\b", low)
    if span:
        start = _iso_bounds(span.group(1))
        end = _iso_bounds(span.group(2), end=True)
        if start and end and start <= end and end < now.isoformat():
            return start, end, span.group(0)
    since = re.search(rf"\bsince\s+({_DATEISH})\b", low)
    if since:
        start = _iso_bounds(since.group(1))
        if start and start < now.isoformat() and int(since.group(1)[:4]) < now.year:
            return start, None, since.group(0)
    year = re.search(rf"\bin\s+({_YEAR})\b", low)
    if year:
        value = year.group(1)
        if int(value) < now.year:
            return _iso_bounds(value), _iso_bounds(value, end=True), year.group(0)
    return None, None, ""


def detect_news_intent(question: str, *, today: Optional[date] = None) -> NewsIntent:
    """Classify a query as news and extract any explicit time window.

    Deterministic and offline. ``today`` is injectable for tests. The window
    is converted to an inclusive ``date_from``/``date_to`` so the orchestrator
    can pass a real recency constraint to backends and post-filters.
    """
    text = " ".join((question or "").split())
    if not text:
        return NewsIntent()
    low = text.lower()
    now = today or _today_utc()

    window_days: Optional[int] = None
    evidence = ""
    explicit = False

    # Explicit hour/day/week/month spans, numeric or word form.
    span = re.search(
        r"\b(?:last|past|previous|within)\s+(\d+|one|two|three|four|five|six|seven|"
        r"eight|nine|ten|eleven|twelve|fourteen|fifteen|twenty|thirty|sixty|ninety)"
        r"\s*(hours?|hrs?|days?|weeks?|months?|years?)\b", low)
    if span:
        n = int(span.group(1)) if span.group(1).isdigit() else _WORD_NUMBERS.get(span.group(1), 0)
        unit = span.group(2)
        if n > 0:
            if unit.startswith(("hour", "hr")):
                window_days = max(1, n // 24)
            elif unit.startswith("day"):
                window_days = n
            elif unit.startswith("week"):
                window_days = n * 7
            elif unit.startswith("month"):
                window_days = n * 30
            elif unit.startswith("year"):
                window_days = n * 365
            evidence = span.group(0)
            explicit = True

    if window_days is None:
        if re.search(r"\b(last|past|previous)\s+24\s*hours?\b", low) or "last 24 hours" in low:
            window_days, evidence, explicit = 1, "last 24 hours", True
        elif re.search(r"\b(today|tonight|this morning|right now|as of today)\b", low):
            window_days, evidence, explicit = 1, "today", True
        elif re.search(r"\byesterday\b", low):
            window_days, evidence, explicit = 2, "yesterday", True
        elif re.search(r"\b(this|the past|the last)\s+week\b", low):
            window_days, evidence, explicit = 7, "this week", True
        elif re.search(r"\b(this|the past|the last)\s+month\b", low):
            window_days, evidence, explicit = 30, "this month", True

    # An archive question names an *older* span instead of a recent one. It is
    # detected before the news-shape test because "climate policy in 2019"
    # contains no recency word at all, yet is unambiguously a news request.
    archive_from, archive_to, archive_evidence = _detect_archive_range(low, now)
    # A bare word like "history" is NOT enough. "history of the roman empire"
    # is an encyclopedic question, and calling it news would send it to a
    # rolling news index and stamp a recency window on it. Word-only archive
    # intent therefore needs a reporting noun ("historical *reporting* on the
    # flood") and must not be contradicted by a recency request ("latest news
    # on the history of AI" is a news question, not an archive dig).
    archive_worded = (
        bool(_ARCHIVE_MARKERS.search(low))
        and bool(_ARCHIVE_REPORTING.search(low))
        and window_days is None
        and not _RECENCY_WORDS.search(low)
    )
    is_archive = bool(archive_evidence) or archive_worded

    is_news = bool(_NEWS_MARKERS.search(text)) or explicit or is_archive
    # A dated statistic ("GDP growth 2026") is not breaking news; the intent
    # classifier in intent.py makes the final statistic-vs-news call. Here we
    # only flag the news shape and the window.
    if not is_news:
        return NewsIntent()

    date_from = date_to = None
    if is_archive and archive_evidence:
        date_from, date_to = archive_from, archive_to
        evidence = archive_evidence
        explicit = True
        window_days = None
    elif window_days is not None:
        date_to = now.isoformat()
        date_from = (now - timedelta(days=max(0, window_days - 1))).isoformat()
    return NewsIntent(
        is_news=True, window_days=window_days, date_from=date_from, date_to=date_to,
        window_evidence=evidence, explicit_window=explicit, is_archive=is_archive,
    )


# --------------------------------------------------------------------------- #
# Timestamp normalization
# --------------------------------------------------------------------------- #

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def normalize_timestamp(value: Any) -> Optional[str]:
    """Normalize a wire timestamp to an ISO ``YYYY-MM-DD`` UTC date, or None.

    Accepts ISO 8601 (with or without zone/offset), RFC 822 (RSS ``pubDate``),
    GDELT ``YYYYMMDDTHHMMSSZ``, and epoch seconds. Ambiguous or unparseable
    values return ``None`` — an unknown date stays unknown.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    # GDELT compact form: 20261019T012345Z
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T\d{6}Z?$", raw)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    # ISO 8601, possibly with trailing Z or offset.
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        pass
    # RFC 822 / RSS pubDate: "Wed, 28 Oct 2026 18:44:23 GMT"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})", raw)
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(1))).isoformat()
            except ValueError:
                return None
    # Bare ISO date.
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Publication-date extraction from a publisher page
# --------------------------------------------------------------------------- #

#: meta names/properties that carry a *publication* time, in priority order.
_PUBLICATION_META = (
    "article:published_time", "og:article:published_time", "parsely-pub-date",
    "dc.date.issued", "dc.date.created", "dcterms.created", "datepublished",
    "publish-date", "publish_date", "publication_date", "sailthru.date",
    "date", "originalpublicationdate",
)
#: meta names/properties that carry a *modification* time (never publication).
_MODIFICATION_META = (
    "article:modified_time", "og:updated_time", "article:updated_time",
    "parsely-pub-date-updated", "dc.date.modified", "dcterms.modified",
    "datemodified", "last-modified", "lastmod", "updated_time",
)


@dataclass
class ArticleDates:
    """Publication and modification times read from a page, kept separate."""

    published_at: Optional[str] = None
    modified_at: Optional[str] = None
    published_evidence: str = ""
    modified_evidence: str = ""
    #: "high" (structured JSON-LD / article meta), "medium" (time tag / generic
    #: meta), "low" (heuristic), "" (none found). A heuristic, not a probability.
    confidence: str = ""
    candidates: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "published_at": self.published_at,
            "modified_at": self.modified_at,
            "published_evidence": self.published_evidence,
            "modified_evidence": self.modified_evidence,
            "date_confidence": self.confidence,
            "date_candidates": self.candidates,
        }


class _HeadMetaParser(HTMLParser):
    """Collect JSON-LD blocks, meta tags, and <time datetime> values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: List[str] = []
        self.meta: List[Tuple[str, str]] = []
        self.times: List[Tuple[str, str]] = []  # (datetime, kind-hint)
        self._in_jsonld = False
        self._jsonld_buf: List[str] = []
        self._time_attrs: Dict[str, str] = {}
        self._in_time = False
        self._time_buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = []
        elif tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop") or ""
            content = a.get("content") or a.get("value") or ""
            if key and content:
                self.meta.append((key.lower(), content))
        elif tag == "time":
            self._in_time = True
            self._time_buf = []
            self._time_attrs = a

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._jsonld_buf))
        elif tag == "time" and self._in_time:
            self._in_time = False
            dt = self._time_attrs.get("datetime") or "".join(self._time_buf).strip()
            hint = (self._time_attrs.get("itemprop") or self._time_attrs.get("class") or "").lower()
            if dt:
                self.times.append((dt, hint))

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        elif self._in_time:
            self._time_buf.append(data)


def _walk_jsonld(obj: Any, found: List[Dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.lower() in (
                "newsarticle", "article", "reportagenewsarticle", "blogposting",
                "scholarlyarticle", "webpage", "report") for x in types):
            found.append(obj)
        for v in obj.values():
            _walk_jsonld(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_jsonld(v, found)


def extract_article_dates(html: str) -> ArticleDates:
    """Read publication/modification dates from a publisher page's metadata.

    Priority: JSON-LD ``datePublished`` (high) → article/publication meta tags
    (high) → ``<time datetime itemprop=datePublished>`` (medium) → generic
    publication meta (medium). Modification times are collected separately and
    are never returned as the publication date. Nothing is guessed from prose.
    """
    if not html or not html.strip():
        return ArticleDates()
    parser = _HeadMetaParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # A malformed page must not crash date extraction; fall through to
        # whatever was collected before the error.
        pass
    return _dates_from_components(parser.jsonld, parser.meta, parser.times)


def _dates_from_components(jsonld_blocks: List[str], meta_pairs: List[Tuple[str, str]],
                           time_pairs: List[Tuple[str, str]]) -> ArticleDates:
    """Apply the date priority/normalization to already-collected components.

    Shared by the static HTML path (``extract_article_dates``) and the browser
    path (``article_dates_from_dom``), so a JS-rendered page and a static page
    are judged by exactly the same rules.
    """
    result = ArticleDates()
    # 1. JSON-LD structured data — the most trustworthy publisher signal.
    articles: List[Dict[str, Any]] = []
    for block in jsonld_blocks:
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        _walk_jsonld(data, articles)
    for art in articles:
        pub = normalize_timestamp(art.get("datePublished") or art.get("uploadDate"))
        mod = normalize_timestamp(art.get("dateModified") or art.get("dateUpdated"))
        if pub and not result.published_at:
            result.published_at = pub
            result.published_evidence = "jsonld:datePublished"
            result.confidence = "high"
            result.candidates.append({"value": pub, "kind": "publication", "source": "jsonld"})
        if mod and not result.modified_at:
            result.modified_at = mod
            result.modified_evidence = "jsonld:dateModified"
            result.candidates.append({"value": mod, "kind": "modification", "source": "jsonld"})

    # 2. Publication meta tags.
    meta = dict(meta_pairs)
    if not result.published_at:
        for name in _PUBLICATION_META:
            if name in meta:
                pub = normalize_timestamp(meta[name])
                if pub:
                    result.published_at = pub
                    result.published_evidence = f"meta:{name}"
                    result.confidence = result.confidence or "high"
                    result.candidates.append({"value": pub, "kind": "publication", "source": f"meta:{name}"})
                    break
    if not result.modified_at:
        for name in _MODIFICATION_META:
            if name in meta:
                mod = normalize_timestamp(meta[name])
                if mod:
                    result.modified_at = mod
                    result.modified_evidence = f"meta:{name}"
                    result.candidates.append({"value": mod, "kind": "modification", "source": f"meta:{name}"})
                    break

    # 3. <time datetime> with a publication hint. The hint is matched
    # case-insensitively so a static ``itemprop`` (lowercased by the parser) and
    # a rendered-DOM ``datePublished`` (camelCase) behave identically.
    if not result.published_at:
        for dt, hint in time_pairs:
            hint_low = (hint or "").lower()
            if "published" in hint_low or "pubdate" in hint_low or hint_low == "":
                pub = normalize_timestamp(dt)
                if pub:
                    result.published_at = pub
                    result.published_evidence = "time:datetime"
                    result.confidence = result.confidence or "medium"
                    result.candidates.append({"value": pub, "kind": "publication", "source": "time"})
                    break
    return result


#: A bounded JS snippet run in the *rendered* DOM (Obscura) to collect the same
#: date-bearing components the static HTML parser collects: JSON-LD blocks,
#: meta name/content pairs, and <time datetime> values. Returns a JSON string
#: so the engine's evaluate unwraps it to a dict. It reads metadata only — it
#: does not navigate, click, or execute page code beyond reading the DOM.
DOM_DATE_COMPONENTS_JS = (
    "(function(){try{var o={jsonld:[],meta:[],times:[]};"
    "document.querySelectorAll('script[type=\"application/ld+json\"]').forEach("
    "function(s){if(s.textContent&&o.jsonld.length<20)o.jsonld.push(s.textContent.slice(0,20000));});"
    "document.querySelectorAll('meta').forEach(function(m){"
    "var k=m.getAttribute('property')||m.getAttribute('name')||m.getAttribute('itemprop');"
    "var v=m.getAttribute('content')||m.getAttribute('value');"
    "if(k&&v&&o.meta.length<200)o.meta.push([k,v]);});"
    "document.querySelectorAll('time[datetime]').forEach(function(t){"
    "if(o.times.length<50)o.times.push([t.getAttribute('datetime'),"
    "t.getAttribute('itemprop')||t.getAttribute('class')||'']);});"
    "return JSON.stringify(o);}catch(e){return JSON.stringify({jsonld:[],meta:[],times:[]});}})()"
)


def article_dates_from_dom(payload: Any) -> ArticleDates:
    """Build ArticleDates from the rendered-DOM components returned by the browser.

    ``payload`` is the dict produced by evaluating ``DOM_DATE_COMPONENTS_JS``
    (``{jsonld: [...], meta: [[k, v], ...], times: [[dt, hint], ...]}``). The
    same priority/normalization as the static path is applied, so a JS-heavy
    publisher page rendered by Obscura yields the same quality of date evidence
    as a static page. Malformed or missing payloads yield an empty (unknown)
    result — never an invented date.
    """
    if not isinstance(payload, dict):
        return ArticleDates()
    jsonld = [b for b in payload.get("jsonld", []) if isinstance(b, str)]
    meta = [(str(k), str(v)) for k, v in payload.get("meta", [])
            if isinstance(k, str) and isinstance(v, str)]
    times = [(str(dt), str(hint or "")) for dt, hint in payload.get("times", [])
             if isinstance(dt, str)]
    return _dates_from_components(jsonld, meta, times)


# --------------------------------------------------------------------------- #
# Source quality / primary-publisher signals (bounded, never a hard filter)
# --------------------------------------------------------------------------- #

#: Established news publishers and primary institutional sources. A hit here
#: is a *bounded* credibility boost in ranking, not a guarantee of accuracy and
#: not a filter that hides other sources.
_ESTABLISHED_PUBLISHERS = frozenset({
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "theguardian.com",
    "nytimes.com", "washingtonpost.com", "wsj.com", "ft.com", "bloomberg.com",
    "cnbc.com", "cnn.com", "npr.org", "pbs.org", "abcnews.go.com", "cbsnews.com",
    "nbcnews.com", "aljazeera.com", "politico.com", "politico.eu", "axios.com",
    "theverge.com", "arstechnica.com", "techcrunch.com", "wired.com",
    "nature.com", "science.org", "sciencemag.org", "nationalgeographic.com",
    "economist.com", "time.com", "newsweek.com", "usatoday.com", "latimes.com",
    "sfchronicle.com", "thehindu.com", "hindustantimes.com", "indianexpress.com",
    "nikkei.com", "japantimes.co.jp", "scmp.com", "dw.com", "france24.com",
    "lemonde.fr", "spiegel.de", "elpais.com", "corriere.it", "globo.com",
    "folha.uol.com.br", "thestreet.com", "marketwatch.com", "barrons.com",
    "coindesk.com", "theblock.co", "insideevs.com", "space.com",
    "spaceflightnow.com", "nasaspaceflight.com", "mongabay.com", "carbonbrief.org",
    "flightglobal.com", "aviationweek.com", "defensenews.com", "breakingdefense.com",
})

#: Primary institutional / government / intergovernmental sources.
_PRIMARY_SUFFIXES = (".gov", ".gov.in", ".gov.uk", ".mil", ".nic.in", ".int",
                     ".europa.eu", ".un.org", ".who.int", ".gc.ca", ".canada.ca")
_PRIMARY_DOMAINS = frozenset({
    "nasa.gov", "nih.gov", "cdc.gov", "noaa.gov", "usgs.gov", "federalreserve.gov",
    "sec.gov", "commerce.gov", "whitehouse.gov", "ec.europa.eu", "ecb.europa.eu",
    "unfccc.int", "who.int", "opec.org", "iea.org", "iter.org", "llnl.gov",
    "inpe.br", "boj.or.jp", "apple.com", "openai.com", "airbus.com", "boeing.com",
    "investor.nvidia.com", "nhc.noaa.gov", "earthquake.usgs.gov", "eci.gov.in",
})


def is_primary_publisher(url: str) -> bool:
    from .urls import domain_of
    host = domain_of(url or "")
    if not host:
        return False
    if host in _PRIMARY_DOMAINS:
        return True
    return any(host == s.lstrip(".") or host.endswith(s) for s in _PRIMARY_SUFFIXES)


def source_quality(url: str) -> float:
    """A bounded [0, 1] credibility signal for a news source.

    Established publishers and primary institutional sources score higher;
    everything else gets a neutral baseline. This is a heuristic prior, not a
    calibrated probability and not a verdict on any single article.
    """
    from .urls import domain_of
    host = domain_of(url or "")
    if not host:
        return 0.5
    if is_primary_publisher(url):
        return 0.95
    for dom in _ESTABLISHED_PUBLISHERS:
        if host == dom or host.endswith("." + dom):
            return 0.85
    return 0.5


# --------------------------------------------------------------------------- #
# Syndication deduplication
# --------------------------------------------------------------------------- #

#: Wire-service / syndication phrasing stripped before comparing titles.
_SYNDICATION_NOISE = re.compile(
    r"\b(reuters|associated press|ap|afp|agence france-presse|bloomberg|cnn|"
    r"bbc|the guardian|guardian|al jazeera|via|syndicated|wire report|"
    r"breaking news|latest news|update[sd]?|live|watch|video|photos?)\b",
    re.IGNORECASE,
)


def syndication_key(title: str) -> str:
    """A normalized title key for cross-domain syndication dedup.

    The same wire story carried by ten outlets shares this key, so the
    orchestrator can collapse copies and prefer the original publisher. It is
    deliberately conservative: only generic outlet/boilerplate words are
    removed, so genuinely different stories are not merged.
    """
    text = (title or "").lower()
    text = _SYNDICATION_NOISE.sub(" ", text)
    terms = sorted(term_set(text))
    return " ".join(terms)


def looks_syndicated(title_a: str, title_b: str, *, threshold: float = 0.8) -> bool:
    """Whether two titles are near-identical wire copies (token Jaccard)."""
    ka = set(syndication_key(title_a).split())
    kb = set(syndication_key(title_b).split())
    if not ka or not kb:
        return False
    return len(ka & kb) / len(ka | kb) >= threshold


__all__ = [
    "NewsIntent", "detect_news_intent", "DEFAULT_NEWS_WINDOW_DAYS",
    "normalize_timestamp", "ArticleDates", "extract_article_dates",
    "article_dates_from_dom", "DOM_DATE_COMPONENTS_JS",
    "source_quality", "is_primary_publisher", "syndication_key", "looks_syndicated",
]
