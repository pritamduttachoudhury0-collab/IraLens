# -*- coding: utf-8 -*-
"""Publisher sitemap / archive discovery — the historical-news lane.

Why this exists: every news *index* is a rolling recent window. GDELT DOC, the
Google/Bing news feeds and the general SERPs cannot answer "climate policy in
2019" — they simply have nothing that old. A publisher's own sitemap and
sitemap index, however, list article URLs going back years, and a news sitemap
carries the publisher's stated publication date.

So for an archive-shaped question ("in 2019", "between 2019 and 2021",
"since 2018-03", or any explicit old date range) this lane reads the
publisher's ``robots.txt`` for its declared ``Sitemap:`` entries, walks a
bounded number of sitemap indexes, and matches article URLs against the query
and the requested window.

Date discipline (stricter than a naive sitemap reader):
* ``<news:publication_date>`` is a **publication** date.
* ``<lastmod>`` is a **revision** date — the page changed then, which is NOT
  when the story was published. It is carried as ``modified_at`` with
  ``date_kind="revision"`` and is never promoted to ``published_at``.
A sitemap row with only ``lastmod`` therefore yields a result whose
publication date is honestly unknown.

Adapted from the sitemap lane of PyNewsBrowser (MIT, (c) 2026 PyNewsBrowser
contributors), rewritten for IraLens: SSRF-safe transport, typed failures,
per-host pacing, bounded crawl, and the publication/revision separation above.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from ...errors import IraLensError, SourceUnavailableError, VerificationRequiredError
from ...fetch import fetch_url_text
from ...reliability import HOST_RATE_LIMITER
from ...search.schema import SearchFilters, SearchHit
from ..news import normalize_timestamp
from ..querytext import term_set
from .base import NATIVE, POST, UNSUPPORTED, SearchBackend

_USER_AGENT = "IraLens/0.3.5 (https://github.com/pritamduttachoudhury0-collab/IraLens)"
_MAX_BYTES = 4 * 1024 * 1024
#: Hard crawl bounds. A sitemap index can fan out to thousands of children;
#: an unbounded walk would hammer a publisher and blow the latency budget.
MAX_SITEMAP_FETCHES = 6
MAX_SITEMAP_DEPTH = 2
MAX_CHILD_INDEXES = 5
MAX_DOMAINS = 3
_SITEMAP_DECL_RE = re.compile(r"(?im)^\s*Sitemap:\s*(\S+)\s*$")


def _local(tag: str) -> str:
    """Strip an XML namespace: '{ns}url' -> 'url'."""
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(xml_text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """Parse a sitemap or sitemap index into (child indexes, url rows).

    Pure and offline. Handles the default sitemap namespace and the Google
    News sitemap extensions without needing to know the exact prefix. A
    malformed document yields empty lists rather than raising, so one bad
    sitemap never aborts the lane.
    """
    indexes: List[str] = []
    rows: List[Dict[str, str]] = []
    if not xml_text or not xml_text.strip():
        return indexes, rows
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return indexes, rows
    for elem in root.iter():
        name = _local(elem.tag)
        if name == "sitemap":
            for child in elem:
                if _local(child.tag) == "loc" and child.text:
                    indexes.append(child.text.strip())
                    break
        elif name == "url":
            row: Dict[str, str] = {}
            for child in elem.iter():
                cn = _local(child.tag)
                if cn == "loc" and child.text and "url" not in row:
                    row["url"] = child.text.strip()
                elif cn == "lastmod" and child.text:
                    row["lastmod"] = child.text.strip()
                elif cn == "publication_date" and child.text:
                    row["publication_date"] = child.text.strip()
                elif cn == "title" and child.text and "title" not in row:
                    row["title"] = " ".join(child.text.split())
                elif cn == "keywords" and child.text:
                    row["keywords"] = " ".join(child.text.split())
            if row.get("url"):
                rows.append(row)
    return indexes, rows


def sitemap_candidates(domain: str, robots_text: str = "") -> List[str]:
    """Sitemap URLs to try for a domain: declared ones first, then conventions.

    A publisher that declares its sitemaps in robots.txt is telling us where
    they are; those are preferred over guessing paths.
    """
    declared = [m.group(1) for m in _SITEMAP_DECL_RE.finditer(robots_text or "")]
    root = f"https://{domain}"
    conventional = [root + "/news-sitemap.xml", root + "/sitemap.xml",
                    root + "/sitemap_index.xml", root + "/sitemap-index.xml"]
    out: List[str] = []
    for url in declared + conventional:
        if url not in out:
            out.append(url)
    return out


def _url_words(url: str) -> str:
    """The words a URL slug is made of.

    ``/2019/climate-policy-deal`` is tokenised as one hyphenated token by the
    shared query tokenizer, so the slug is split on its separators first.
    Without this a sitemap row could never match a plain-word query.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    path = parts.path or ""
    path = re.sub(r"\.(html?|php|aspx?|cms)$", " ", path, flags=re.IGNORECASE)
    return re.sub(r"[/\-_.]+", " ", path).lower()


def _row_matches(row: Dict[str, str], q_terms: set) -> bool:
    """Whether a sitemap row plausibly addresses the query.

    Sitemaps carry no body text, so the signal is the URL slug, the news
    title and the news keywords. Any query term present is enough to keep the
    candidate; ranking and the page read decide the rest.
    """
    if not q_terms:
        return True
    haystack = " ".join((_url_words(row.get("url", "")), row.get("title", ""),
                         row.get("keywords", ""))).lower()
    haystack_terms = term_set(haystack)
    return bool(q_terms & haystack_terms)


def _in_window(value: Optional[str], date_from: Optional[str], date_to: Optional[str]) -> bool:
    if not value:
        # An undated sitemap row is kept: the window is enforced on known
        # dates, and the read path can still establish the real date.
        return True
    if date_from and value < date_from:
        return False
    if date_to and value > date_to:
        return False
    return True


def rows_to_hits(rows: List[Dict[str, str]], query: str, limit: int, *,
                 domain: str, date_from: Optional[str] = None,
                 date_to: Optional[str] = None) -> List[SearchHit]:
    """Turn sitemap rows into hits, keeping publication and revision apart."""
    q_terms = term_set(query)
    hits: List[SearchHit] = []
    seen: set = set()
    for row in rows:
        if len(hits) >= max(0, limit):
            break
        url = row.get("url", "")
        if not url or url in seen:
            continue
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            continue
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            continue
        if not _row_matches(row, q_terms):
            continue
        published = normalize_timestamp(row.get("publication_date"))
        modified = normalize_timestamp(row.get("lastmod"))
        # The window applies to whichever date we actually have. A row with
        # only a revision date is filtered on that revision date, but is never
        # labelled as published then.
        window_date = published or modified
        if not _in_window(window_date, date_from, date_to):
            continue
        seen.add(url)
        title = row.get("title") or _title_from_slug(url)
        raw: Dict[str, Any] = {"provider": "sitemaps", "domain": domain,
                               "discovered_via": "sitemap"}
        if published:
            raw["date_evidence"] = "sitemap:news:publication_date"
        elif modified:
            raw["date_evidence"] = "sitemap:lastmod"
            raw["date_is_revision_only"] = True
        hits.append(SearchHit(
            title=title[:240], url=url, snippet="", engine="sitemaps",
            position=len(hits) + 1, query=query, raw=raw,
            published_at=published, modified_at=modified,
            date_kind="publication" if published else ("revision" if modified else ""),
        ))
    return hits


def _title_from_slug(url: str) -> str:
    """A readable fallback title from the URL slug (never a fabricated claim)."""
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return url
    slug = [s for s in path.split("/") if s]
    if not slug:
        return url
    last = re.sub(r"\.(html?|php|aspx?)$", "", slug[-1], flags=re.IGNORECASE)
    last = re.sub(r"[-_]+", " ", last).strip()
    return last[:240] or url


class SitemapsBackend(SearchBackend):
    """Archive discovery from publisher sitemaps (keyless, bounded, polite)."""

    name = "sitemaps"
    paginated = False
    handled = {"date_from": NATIVE, "date_to": NATIVE, "include_domains": NATIVE,
               "exclude_domains": POST, "file_type": UNSUPPORTED,
               "language": UNSUPPORTED, "region": POST}

    def _domains(self, filters: SearchFilters) -> List[str]:
        if filters.include_domains:
            # Caller-scoped: respect their choice, bounded.
            return list(filters.include_domains)[:MAX_DOMAINS]
        from ..sources_pack import archive_domains
        # Deduplicated: several curated feeds share a publisher domain, and
        # spending the whole domain budget on one publisher wastes the lane.
        return archive_domains(limit=MAX_DOMAINS)

    def search(self, query: str, filters: SearchFilters, limit: int, context: Any,
               *, offset: int = 0) -> List[SearchHit]:
        if offset:
            raise SourceUnavailableError("sitemaps does not support verified pagination",
                                         detail="pagination_unsupported")
        settings = getattr(context, "config", None)
        try:
            configured = max(1, int(settings.get("search_timeout_seconds", 10))) if settings else 10
        except (TypeError, ValueError):
            configured = 10
        timeout = min(configured, 10)
        domains = self._domains(filters)
        if not domains:
            raise SourceUnavailableError("no publisher domains available for archive discovery",
                                         detail="no_domains")
        hits: List[SearchHit] = []
        failures: List[str] = []
        walls: List[VerificationRequiredError] = []
        for domain in domains:
            try:
                hits.extend(self._for_domain(domain, query, filters, limit, timeout))
            except VerificationRequiredError as exc:
                walls.append(exc)
                failures.append(f"{domain}: human verification required")
            except IraLensError as exc:
                failures.append(f"{domain}: {getattr(exc, 'detail', '') or exc}")
            except Exception as exc:  # one publisher must not abort the lane
                failures.append(f"{domain}: {type(exc).__name__}")
            if len(hits) >= limit:
                break
        if not hits and failures and len(failures) == len(domains):
            # If every publisher asked for a human, say exactly that instead of
            # flattening it into a generic outage.
            if len(walls) == len(domains):
                raise walls[0]
            raise SourceUnavailableError("publisher sitemaps could not be read",
                                         detail="; ".join(failures)[:300])
        return hits[:max(1, limit)]

    def _fetch(self, url: str, timeout: int) -> str:
        host = urllib.parse.urlsplit(url).hostname or ""
        HOST_RATE_LIMITER.set_interval(host, 1.0)
        HOST_RATE_LIMITER.wait(host)
        return fetch_url_text(url, timeout=timeout, max_bytes=_MAX_BYTES,
                              headers={"Accept": "application/xml, text/xml, */*",
                                       "User-Agent": _USER_AGENT},
                              detect_challenge=True)

    def _for_domain(self, domain: str, query: str, filters: SearchFilters,
                    limit: int, timeout: int) -> List[SearchHit]:
        robots_text = ""
        try:
            robots_text = self._fetch(f"https://{domain}/robots.txt", timeout)
        except Exception:
            # A missing or blocked robots.txt is not fatal: the conventional
            # sitemap paths are still worth one bounded try.
            robots_text = ""
        queue = sitemap_candidates(domain, robots_text)
        attempts = 0
        readable = 0
        depth = 0
        rows: List[Dict[str, str]] = []
        errors: List[str] = []
        while queue and attempts < MAX_SITEMAP_FETCHES and depth <= MAX_SITEMAP_DEPTH:
            url = queue.pop(0)
            # An *attempt* costs budget, not a success. Counting only successes
            # lets a publisher whose sitemaps all 404 drain an arbitrarily long
            # index one request at a time — the bound would be decorative.
            attempts += 1
            try:
                text = self._fetch(url, timeout)
            except VerificationRequiredError:
                # A human-verification wall is a distinct condition, not a
                # generic outage. Keep it typed so the caller is told the truth.
                raise
            except IraLensError as exc:
                errors.append(f"{url}: {getattr(exc, 'detail', '') or type(exc).__name__}")
                continue
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}")
                continue
            readable += 1
            indexes, url_rows = parse_sitemap(text)
            rows.extend(url_rows)
            if indexes:
                queue.extend(indexes[:MAX_CHILD_INDEXES])
                depth += 1
            if len(rows) >= limit * 20:
                break
        # No article rows plus at least one failure is a failure, not an empty
        # archive. Returning [] here would report "no historical coverage
        # exists" when the truth is "the sitemaps could not be read". This also
        # covers a readable *index* whose every child failed: an index alone
        # lists no articles. A genuinely empty sitemap has no errors and stays
        # a quiet, honest zero.
        if not rows and errors:
            raise SourceUnavailableError(f"no sitemap could be read for {domain}",
                                         detail="; ".join(errors)[:300])
        return rows_to_hits(rows, query, limit, domain=domain,
                            date_from=filters.date_from, date_to=filters.date_to)


__all__ = ["SitemapsBackend", "parse_sitemap", "sitemap_candidates", "rows_to_hits"]
