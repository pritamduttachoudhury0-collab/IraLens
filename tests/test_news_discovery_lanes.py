# -*- coding: utf-8 -*-
"""Offline contracts for the archive, publisher-feed and story-level lanes.

These cover the gaps that a rolling news index cannot fill:

* **archive questions** ("climate policy in 2019") — GDELT DOC and the news
  feeds hold a recent window only, so a historical question had no source at
  all. Publisher sitemaps still list the old articles.
* **local / regional coverage** — global indexes surface Assam and Northeast
  India outlets late or never; the curated publisher feeds reach them directly.
* **story-level answers** — eight copies of one wire report are one story, and
  a developing event has a first report and a latest update.
* **human-verification walls** — detected, classified and reported honestly.
  IraLens does not defeat a CAPTCHA or a "prove you are not a robot" check.

Everything here is deterministic and offline. The new lanes cannot be
live-validated in a sandbox with an egress allowlist, so the parsers, the
bounds and the failure paths are what these tests pin down.

Sitemap and clustering approaches adapted from PyNewsBrowser (MIT,
(c) 2026 PyNewsBrowser contributors); see the module docstrings.
"""
from __future__ import annotations

from datetime import date

import pytest

from iralens import challenge, fetch
from iralens.config import Config
from iralens.errors import SourceUnavailableError, VerificationRequiredError
from iralens.search import news
from iralens.search.engines.publisher_feeds import (
    MAX_FEEDS_PER_QUERY, PublisherFeedsBackend, filter_feed_hits,
)
from iralens.search.engines.sitemaps import (
    MAX_DOMAINS, MAX_SITEMAP_FETCHES, SitemapsBackend,
    parse_sitemap, rows_to_hits, sitemap_candidates,
)
from iralens.search.events import (
    _story_similarity, build_timeline, cluster_results, detect_trends,
)
from iralens.search.schema import SearchFilters, SearchHit, SearchResponse
from iralens.search.sources_pack import (
    PUBLISHER_FEEDS, archive_domains, detect_region, detect_topics,
    region_matches, select_feeds,
)

TODAY = date(2026, 11, 4)


class Context:
    def __init__(self) -> None:
        self.config = Config(read_only=True)


# --------------------------------------------------------------------------- #
# Fixtures: sitemap XML in both the namespaced and the bare form
# --------------------------------------------------------------------------- #

NEWS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://publisher.test/2019/03/climate-policy-accord</loc>
    <lastmod>2026-10-30T09:00:00Z</lastmod>
    <news:news>
      <news:publication><news:name>Publisher</news:name></news:publication>
      <news:publication_date>2019-03-14T08:00:00Z</news:publication_date>
      <news:title>Climate policy accord signed after long talks</news:title>
      <news:keywords>climate, policy, accord</news:keywords>
    </news:news>
  </url>
  <url>
    <loc>https://publisher.test/2019/07/climate-old-story</loc>
    <lastmod>2019-07-02T10:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://publisher.test/2021/01/unrelated-sport-report</loc>
    <lastmod>2021-01-05T10:00:00Z</lastmod>
  </url>
</urlset>
"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://publisher.test/sitemap-2019.xml</loc>
           <lastmod>2020-01-01</lastmod></sitemap>
  <sitemap><loc>https://publisher.test/sitemap-2021.xml</loc></sitemap>
</sitemapindex>
"""

BARE_SITEMAP = """<urlset><url>
  <loc>https://publisher.test/2019/climate-policy-bare</loc>
  <lastmod>2019-05-05</lastmod>
</url></urlset>"""

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Assam Tribune</title>
  <item>
    <title>Assam floods displace thousands in Barpeta</title>
    <link>https://assamtribune.com/floods-barpeta</link>
    <description>Flood waters displaced thousands of residents.</description>
    <pubDate>Mon, 02 Nov 2026 08:00:00 GMT</pubDate>
  </item>
  <item>
    <title>City cricket league opens next month</title>
    <link>https://assamtribune.com/cricket-league</link>
    <pubDate>Tue, 03 Nov 2026 08:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""


# --------------------------------------------------------------------------- #
# Sitemap parsing: publication date is not a revision date
# --------------------------------------------------------------------------- #

def test_sitemap_index_children_are_separated_from_url_rows():
    children, rows = parse_sitemap(SITEMAP_INDEX)
    assert children == ["https://publisher.test/sitemap-2019.xml",
                        "https://publisher.test/sitemap-2021.xml"]
    assert rows == []


def test_sitemap_publication_date_and_lastmod_are_kept_apart():
    children, rows = parse_sitemap(NEWS_SITEMAP)
    assert children == []
    first = rows[0]
    assert first["publication_date"].startswith("2019-03-14")
    assert first["lastmod"].startswith("2026-10-30")
    assert first["title"] == "Climate policy accord signed after long talks"
    # The row that only has a revision date must not gain a publication date.
    assert "publication_date" not in rows[1] or not rows[1]["publication_date"]


def test_sitemap_parser_is_namespace_agnostic():
    _, rows = parse_sitemap(BARE_SITEMAP)
    assert len(rows) == 1
    assert rows[0]["url"].endswith("climate-policy-bare")


def test_lastmod_only_row_is_labelled_a_revision_not_a_publication():
    """The defect in the ported source: lastmod promoted to publication date.

    A page edited in 2026 is not a page published in 2026. Calling a revision
    a publication date is how an old article gets presented as fresh news.
    """
    _, rows = parse_sitemap(NEWS_SITEMAP)
    hits = rows_to_hits(rows, "climate policy", 10, domain="publisher.test")
    by_url = {h.url: h for h in hits}
    published = by_url["https://publisher.test/2019/03/climate-policy-accord"]
    assert published.date_kind == "publication"
    assert published.published_at.startswith("2019-03-14")
    assert published.modified_at.startswith("2026-10-30")

    revision = by_url["https://publisher.test/2019/07/climate-old-story"]
    assert revision.date_kind == "revision"
    assert revision.published_at is None
    assert revision.raw["date_is_revision_only"] is True


def test_hyphenated_url_slug_is_tokenized_for_matching():
    """`term_set` keeps 'climate-old-story' as one token.

    Matching a sitemap URL naively therefore returns nothing at all, which
    looks like 'the archive is empty' rather than 'the matcher is broken'.
    """
    _, rows = parse_sitemap(NEWS_SITEMAP)
    hits = rows_to_hits(rows, "climate old story", 10, domain="publisher.test")
    assert any(h.url.endswith("climate-old-story") for h in hits)


def test_sitemap_rows_outside_the_window_are_dropped():
    _, rows = parse_sitemap(NEWS_SITEMAP)
    hits = rows_to_hits(rows, "climate", 10, domain="publisher.test",
                        date_from="2019-01-01", date_to="2019-12-31")
    assert hits
    assert all(h.url.startswith("https://publisher.test/2019/") for h in hits)


def test_sitemap_rows_with_unsafe_schemes_are_rejected():
    rows = [
        {"url": "javascript:alert(1)", "title": "climate policy"},
        {"url": "file:///etc/passwd", "title": "climate policy"},
        {"url": "http://127.0.0.1/climate-policy", "title": "climate policy"},
        {"url": "https://publisher.test/climate-policy", "title": "climate policy"},
    ]
    hits = rows_to_hits(rows, "climate policy", 10, domain="publisher.test")
    # The loopback URL is a *row*, not a fetch target; the transport guard is
    # what refuses to fetch it. Non-HTTP schemes must never become hits.
    assert all(h.url.startswith(("http://", "https://")) for h in hits)
    assert not any(h.url.startswith(("javascript:", "file:")) for h in hits)


def test_sitemap_duplicate_urls_are_collapsed():
    rows = [{"url": "https://publisher.test/climate-policy", "title": "Climate policy"},
            {"url": "https://publisher.test/climate-policy", "title": "Climate policy"}]
    assert len(rows_to_hits(rows, "climate policy", 10, domain="publisher.test")) == 1


def test_sitemap_candidates_prefer_the_robots_declared_entry():
    robots = ("User-agent: *\nDisallow: /admin\n"
              "Sitemap: https://publisher.test/custom-map.xml\n")
    candidates = sitemap_candidates("publisher.test", robots)
    assert candidates[0] == "https://publisher.test/custom-map.xml"
    assert len(candidates) <= MAX_SITEMAP_FETCHES


def test_sitemap_candidates_fall_back_to_conventional_paths():
    candidates = sitemap_candidates("publisher.test", "")
    assert candidates
    assert all(c.startswith("https://publisher.test/") for c in candidates)


# --------------------------------------------------------------------------- #
# Sitemap backend: bounded, polite, typed failures
# --------------------------------------------------------------------------- #

def _sitemap_backend(monkeypatch, table):
    backend = SitemapsBackend()
    seen: list = []

    def fake_fetch(url, timeout):
        seen.append(url)
        if url not in table:
            raise SourceUnavailableError("offline test", detail="not_found")
        return table[url]

    monkeypatch.setattr(backend, "_fetch", fake_fetch)
    return backend, seen


def test_sitemap_backend_finds_an_archive_article(monkeypatch):
    table = {"https://bbc.com/robots.txt": "Sitemap: https://bbc.com/map.xml\n",
             "https://bbc.com/map.xml": NEWS_SITEMAP}
    backend, seen = _sitemap_backend(monkeypatch, table)
    hits = backend.search("climate policy accord",
                          SearchFilters.build(include_domains=["bbc.com"],
                                              date_from="2019-01-01",
                                              date_to="2019-12-31"),
                          5, Context())
    assert hits
    assert hits[0].published_at.startswith("2019-03-14")
    assert hits[0].raw["discovered_via"] == "sitemap"


def test_sitemap_crawl_is_bounded(monkeypatch):
    """A sitemap index can nest forever; the fetch budget must hold."""
    index = "".join(f"<sitemap><loc>https://bbc.com/m{i}.xml</loc></sitemap>"
                    for i in range(40))
    table = {"https://bbc.com/robots.txt": "",
             "https://bbc.com/sitemap.xml": f"<sitemapindex>{index}</sitemapindex>"}
    backend, seen = _sitemap_backend(monkeypatch, table)
    # The children are all unreadable in this table, so the lane reports a
    # typed failure; what matters here is how many requests it made first.
    with pytest.raises(SourceUnavailableError):
        backend.search("climate", SearchFilters.build(include_domains=["bbc.com"]),
                       5, Context())
    assert len(seen) <= MAX_SITEMAP_FETCHES + 1  # + robots.txt


def test_sitemap_domain_budget_is_bounded_and_deduplicated(monkeypatch):
    backend, seen = _sitemap_backend(monkeypatch, {})
    domains = backend._domains(SearchFilters.build())
    assert len(domains) <= MAX_DOMAINS
    assert len(domains) == len(set(domains))


def test_sitemap_total_failure_is_a_typed_error(monkeypatch):
    backend, _ = _sitemap_backend(monkeypatch, {})
    with pytest.raises(SourceUnavailableError):
        backend.search("climate policy",
                       SearchFilters.build(include_domains=["bbc.com"]), 5, Context())


def test_sitemap_backend_refuses_unverified_pagination():
    with pytest.raises(SourceUnavailableError):
        SitemapsBackend().search("climate", SearchFilters.build(), 5, Context(), offset=10)


# --------------------------------------------------------------------------- #
# Curated publisher pack: regional reach and honest metadata
# --------------------------------------------------------------------------- #

def test_assam_query_selects_assam_publishers():
    feeds = select_feeds("Assam floods last 7 days", limit=4)
    domains = {f.domain for f in feeds}
    assert domains & {"assamtribune.com", "sentinelassam.com"}, domains


def test_northeast_query_is_detected_as_a_region():
    assert detect_region("Assam floods") == "IN-AS"
    assert detect_region("northeast power grid") == "IN-NE"
    assert detect_region("US election results") in ("US", None)


def test_country_region_matches_its_sub_regions():
    assert region_matches("IN-AS", "IN")
    assert region_matches("IN-NE", "IN")
    assert not region_matches("US", "IN")


def test_topic_hints_select_technology_feeds():
    assert "technology" in detect_topics("latest AI chip news")
    feeds = select_feeds("AI chip news", limit=4)
    assert feeds


def test_archive_domains_are_distinct_publishers():
    """The ported source took [:3] of a list where one publisher appears twice.

    That silently spends the whole domain budget on two publishers.
    """
    domains = archive_domains(3)
    assert len(domains) == 3
    assert len(set(domains)) == 3


def test_every_curated_feed_is_public_https_and_well_formed():
    from iralens.security import normalize_public_http_url
    for feed in PUBLISHER_FEEDS:
        assert feed.url.startswith("https://"), feed.url
        assert feed.name and feed.domain and feed.region and feed.language
        assert feed.tier in (1, 2)
        normalize_public_http_url(feed.url)  # raises if not a clean public target


def test_feed_selection_is_bounded():
    assert len(select_feeds("news", limit=MAX_FEEDS_PER_QUERY)) <= MAX_FEEDS_PER_QUERY


# --------------------------------------------------------------------------- #
# Publisher feeds: a firehose must be filtered to the question
# --------------------------------------------------------------------------- #

def _feed_hit(title, url, published=None):
    return SearchHit(title=title, url=url, snippet="", engine="publisher-feeds",
                     position=1, published_at=published, date_kind="publication" if published else "")


def test_feed_items_that_do_not_address_the_query_are_dropped():
    hits = [_feed_hit("Assam floods displace thousands", "https://a.test/1", "2026-11-02"),
            _feed_hit("City cricket league opens", "https://a.test/2", "2026-11-03")]
    kept = filter_feed_hits(hits, "Assam floods")
    assert [h.url for h in kept] == ["https://a.test/1"]


def test_feed_window_filter_applies_to_publication_dates():
    hits = [_feed_hit("Assam floods update", "https://a.test/1", "2026-11-02"),
            _feed_hit("Assam floods archive", "https://a.test/2", "2019-05-05")]
    kept = filter_feed_hits(hits, "Assam floods", date_from="2026-10-29", date_to="2026-11-04")
    assert [h.url for h in kept] == ["https://a.test/1"]


def test_feed_positions_are_renumbered_after_filtering():
    hits = [_feed_hit("Assam floods one", "https://a.test/1", "2026-11-02"),
            _feed_hit("Cricket", "https://a.test/2", "2026-11-03"),
            _feed_hit("Assam floods two", "https://a.test/3", "2026-11-04")]
    kept = filter_feed_hits(hits, "Assam floods")
    assert [h.position for h in kept] == [1, 2]


def test_publisher_feed_backend_reads_and_tags_a_feed(monkeypatch):
    import iralens.search.engines.publisher_feeds as mod
    monkeypatch.setattr(mod, "fetch_url_text", lambda *a, **k: FEED_XML)
    monkeypatch.setattr(mod.HOST_RATE_LIMITER, "wait", lambda host: None)
    hits = PublisherFeedsBackend().search("Assam floods", SearchFilters.build(), 5, Context())
    assert hits
    assert hits[0].raw["discovered_via"] == "publisher_feed"
    assert hits[0].raw["publisher"]
    assert hits[0].raw["source_tier"] in (1, 2)
    assert hits[0].published_at is not None


def test_one_failing_feed_does_not_abort_the_lane(monkeypatch):
    """`pool.map` re-raises on iteration, so failures must be captured inside
    the worker. Otherwise one dead publisher kills every other publisher."""
    import iralens.search.engines.publisher_feeds as mod

    def sometimes(url, **kw):
        if "assamtribune" in url:
            raise SourceUnavailableError("offline test", detail="dead_feed")
        return FEED_XML

    monkeypatch.setattr(mod, "fetch_url_text", sometimes)
    monkeypatch.setattr(mod.HOST_RATE_LIMITER, "wait", lambda host: None)
    hits = PublisherFeedsBackend().search("Assam floods", SearchFilters.build(), 5, Context())
    assert hits, "a single dead feed must not empty the lane"


def test_all_feeds_failing_is_a_typed_error(monkeypatch):
    import iralens.search.engines.publisher_feeds as mod

    def always_fail(url, **kw):
        raise SourceUnavailableError("offline test", detail="dead_feed")

    monkeypatch.setattr(mod, "fetch_url_text", always_fail)
    monkeypatch.setattr(mod.HOST_RATE_LIMITER, "wait", lambda host: None)
    with pytest.raises(SourceUnavailableError):
        PublisherFeedsBackend().search("Assam floods", SearchFilters.build(), 5, Context())


def test_publisher_feed_backend_refuses_unverified_pagination():
    with pytest.raises(SourceUnavailableError):
        PublisherFeedsBackend().search("Assam floods", SearchFilters.build(), 5,
                                       Context(), offset=10)


# --------------------------------------------------------------------------- #
# Story clustering
# --------------------------------------------------------------------------- #

def _ranked(title, url, published=None, score=0.8):
    """A ranked result, which is what the story-level view actually consumes."""
    from iralens.search.schema import RankedResult
    return RankedResult(
        title=title, url=url, canonical_url=url, snippet="", score=score,
        score_breakdown={}, engines=["bing-news"], engine_ranks={"bing-news": 1},
        published_at=published, date_status="known" if published else "unknown",
        queries=[], security_flags=[],
        date_kind="publication" if published else "")


def _response(**kw):
    from iralens.search.schema import SearchResponse
    return SearchResponse(results=[], queries=[], outcomes=[], fallbacks=[],
                          filters={}, cache={}, dedup_log=[],
                          no_results_reason="none", **kw)


def test_inflected_headlines_are_one_story():
    """'displace' and 'displaced' are the same event, not two stories."""
    assert _story_similarity("Floods hit Assam, thousands displaced",
                             "Assam floods displace thousands") >= 0.62


def test_opposite_actions_stay_separate_stories():
    assert _story_similarity("RBI holds repo rate", "RBI cuts repo rate") < 0.62


def test_different_places_stay_separate_stories():
    assert _story_similarity("Floods hit Assam", "Floods hit Kerala") < 0.62


def test_three_outlets_one_wire_story_becomes_one_cluster():
    results = [
        _ranked("Floods hit Assam, thousands displaced", "https://assamtribune.com/a", "2026-11-02"),
        _ranked("Assam floods displace thousands", "https://sentinelassam.com/b", "2026-11-03"),
        _ranked("Assam floods: thousands displaced", "https://thehindu.com/c", "2026-11-04"),
        _ranked("RBI holds repo rate steady", "https://livemint.com/d", "2026-11-03"),
    ]
    clusters = cluster_results(results, today=TODAY)
    assert len(clusters) == 2
    flood = max(clusters, key=lambda c: c.story_count)
    assert flood.story_count == 3
    assert flood.source_count == 3
    assert flood.first_report == "2026-11-02"
    assert flood.latest_update == "2026-11-04"


def test_cluster_primary_link_prefers_the_stronger_publisher():
    results = [
        _ranked("Assam floods displace thousands", "https://unknown-aggregator.test/a", "2026-11-02"),
        _ranked("Assam floods displace thousands", "https://thehindu.com/b", "2026-11-02"),
    ]
    clusters = cluster_results(results, today=TODAY)
    assert len(clusters) == 1
    assert "thehindu.com" in clusters[0].primary_url


def test_clustering_is_deterministic():
    results = [_ranked(f"Assam floods update {i}", f"https://p{i}.test/a", "2026-11-02")
               for i in range(6)]
    first = [c.id for c in cluster_results(results, today=TODAY)]
    second = [c.id for c in cluster_results(list(reversed(results)), today=TODAY)]
    assert first == second


def test_cluster_threshold_argument_is_honoured():
    results = [_ranked("Assam floods displace thousands", "https://a.test/1", "2026-11-02"),
               _ranked("Assam floods displace thousands across state", "https://b.test/2", "2026-11-02")]
    assert len(cluster_results(results, threshold=0.99, today=TODAY)) == 2
    assert len(cluster_results(results, threshold=0.3, today=TODAY)) == 1


def test_timeline_uses_publication_dates_only_and_explains_exclusions():
    results = [
        _ranked("Assam floods displace thousands", "https://a.test/1", "2026-11-02"),
        _ranked("Assam floods latest situation", "https://b.test/2", "2026-11-04"),
        _ranked("Undated market wrap", "https://c.test/3", None),
    ]
    timeline = build_timeline("Assam floods", results, today=TODAY)
    assert timeline.first_report == "2026-11-02"
    assert timeline.latest_update == "2026-11-04"
    assert timeline.dated_story_count < timeline.story_count
    assert "no verifiable publication date" in timeline.note


def test_revision_dates_never_become_a_timeline_position():
    hit = _ranked("Assam floods explained", "https://a.test/1", None)
    hit.modified_at = "2026-11-04"
    hit.date_kind = "revision"
    timeline = build_timeline("Assam floods", [hit], today=TODAY)
    assert timeline.first_report is None
    assert timeline.latest_update is None


def test_trend_signals_carry_the_counts_they_were_computed_from():
    results = [
        _ranked("Floods hit Assam, thousands displaced", "https://assamtribune.com/a", "2026-11-02"),
        _ranked("Assam floods displace thousands", "https://sentinelassam.com/b", "2026-11-03"),
        _ranked("Assam floods: thousands displaced", "https://thehindu.com/c", "2026-11-04"),
    ]
    signals = detect_trends("Assam floods", results, today=TODAY)
    assert signals
    top = signals[0]
    assert top.source_count == 3
    assert any("3 distinct publisher" in e for e in top.evidence), top.evidence
    assert 0.0 < top.score < 10.0


def test_trend_scores_are_ordered_by_coverage():
    results = [
        _ranked("Floods hit Assam, thousands displaced", "https://a.test/1", "2026-11-03"),
        _ranked("Assam floods displace thousands", "https://b.test/2", "2026-11-03"),
        _ranked("Assam floods thousands displaced", "https://c.test/3", "2026-11-03"),
        _ranked("Unrelated cricket league opens", "https://d.test/4", "2026-11-03"),
    ]
    signals = detect_trends("Assam floods", results, today=TODAY)
    assert signals[0].source_count >= signals[-1].source_count
    assert signals == sorted(signals, key=lambda s: -s.score)


def test_no_results_produces_no_invented_story():
    assert cluster_results([], today=TODAY) == []
    assert detect_trends("Assam floods", [], today=TODAY) == []
    timeline = build_timeline("Assam floods", [], today=TODAY)
    assert timeline.story_count == 0
    assert timeline.first_report is None


# --------------------------------------------------------------------------- #
# Archive intent
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("query,start,end", [
    ("climate policy in 2019", "2019-01-01", "2019-12-31"),
    ("oil prices between 2019 and 2021", "2019-01-01", "2021-12-31"),
    ("trade war from 2018-03 to 2018-09", "2018-03-01", "2018-09-30"),
    ("since 2018-03-01 coverage of the border", "2018-03-01", None),
])
def test_archive_windows_are_inclusive_iso_bounds(query, start, end):
    intent = news.detect_news_intent(query, today=TODAY)
    assert intent.is_news and intent.is_archive
    assert intent.date_from == start
    assert intent.date_to == end
    assert intent.explicit_window is True
    assert intent.window_days is None


def test_word_only_archive_needs_a_reporting_noun():
    intent = news.detect_news_intent("historical reporting on the flood", today=TODAY)
    assert intent.is_archive
    assert intent.date_from is None


def test_encyclopedic_history_is_not_news():
    """'history of the roman empire' must not get a recency window.

    A bare 'history' used to make this a news query, which sent it to a
    rolling news index and stamped a timespan on it.
    """
    intent = news.detect_news_intent("history of the roman empire", today=TODAY)
    assert intent.is_news is False
    assert intent.is_archive is False


def test_recency_overrides_archive_wording():
    intent = news.detect_news_intent("latest news on the history of AI", today=TODAY)
    assert intent.is_news is True
    assert intent.is_archive is False


def test_current_year_is_not_an_archive_dig():
    intent = news.detect_news_intent("in 2026 election news", today=TODAY)
    assert intent.is_news is True
    assert intent.is_archive is False


def test_non_news_question_is_untouched():
    intent = news.detect_news_intent("what is the capital of Australia", today=TODAY)
    assert intent.is_news is False


# --------------------------------------------------------------------------- #
# Human-verification walls: detected and reported, never defeated
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,kind", [
    ("Prove you are not a robot", challenge.KIND_BOT_CHALLENGE),
    ("Please confirm you are a human to continue", challenge.KIND_BOT_CHALLENGE),
    ("Verify that you are human", challenge.KIND_BOT_CHALLENGE),
    ('<div id="px-captcha"></div>', challenge.KIND_CAPTCHA),
    ('<script src="https://geo.captcha-delivery.com/c.js"></script>', challenge.KIND_CAPTCHA),
    ('<div class="g-recaptcha" data-sitekey="x"></div>', challenge.KIND_CAPTCHA),
])
def test_human_verification_wording_is_detected(text, kind):
    verdict = challenge.detect(text=text, http_status=403)
    assert verdict.kind == kind
    assert verdict.challenge is True


def test_press_and_hold_is_attributed_to_its_provider():
    verdict = challenge.detect(
        text="<title>Pardon Our Interruption</title>"
             "<script src='/px/client/main.min.js'></script>",
        http_status=200)
    assert verdict.provider == "perimeterx"
    assert verdict.confident is True


def test_an_article_about_captchas_is_not_a_confident_block():
    """Detection must not censor reporting *about* bot checks."""
    body = ("<html><body><p>An explainer on bot checks. "
            "'Prove you are not a robot' is a familiar prompt.</p>"
            + "<p>Ordinary article text.</p>" * 80 + "</body></html>")
    verdict = challenge.detect(text=body, http_status=200)
    assert verdict.confident is False, "an article quoting a prompt is not a wall"


def test_verification_wall_is_a_typed_error_not_content(monkeypatch):
    wall = (b"<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
            b"<body>Checking your browser before accessing. "
            b"Ray ID: 8f3a91bc22</body></html>")

    class Resp:
        def __enter__(self):
            import io
            return io.BytesIO(wall)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetch, "safe_urlopen", lambda *a, **k: Resp())
    with pytest.raises(VerificationRequiredError) as excinfo:
        fetch.fetch_url_text("https://publisher.test/feed.xml", detect_challenge=True)
    data = excinfo.value.to_dict()
    assert data["error"] == "verification_required"
    assert data["verification"]["kind"] == challenge.KIND_BOT_CHALLENGE
    # The honest hint: reported, not bypassed.
    assert "not bypassed" in data["hint"]


def test_xml_feed_mentioning_a_block_page_is_not_misread(monkeypatch):
    body = (b'<?xml version="1.0"?><rss><channel><item>'
            b'<title>Access denied: cloudflare outage explained</title>'
            b'<link>https://publisher.test/a</link></item></channel></rss>')

    class Resp:
        def __enter__(self):
            import io
            return io.BytesIO(body)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetch, "safe_urlopen", lambda *a, **k: Resp())
    text = fetch.fetch_url_text("https://publisher.test/feed.xml", detect_challenge=True)
    assert "Access denied" in text


def test_challenge_detection_is_opt_in(monkeypatch):
    wall = b"<html><body>Checking your browser before accessing. Ray ID: abc123def</body></html>"

    class Resp:
        def __enter__(self):
            import io
            return io.BytesIO(wall)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetch, "safe_urlopen", lambda *a, **k: Resp())
    # Default callers (e.g. a JSON API) are unaffected.
    assert "Checking your browser" in fetch.fetch_url_text("https://publisher.test/x")


# --------------------------------------------------------------------------- #
# Response schema: the story-level view must survive serialization
# --------------------------------------------------------------------------- #

def test_events_and_trends_survive_a_serialization_roundtrip():
    results = [_ranked("Assam floods displace thousands", "https://a.test/1", "2026-11-02")]
    response = _response(
        query="Assam floods",
        events=build_timeline("Assam floods", results, today=TODAY).to_dict(),
        trends=[s.to_dict() for s in detect_trends("Assam floods", results, today=TODAY)],
    )
    restored = SearchResponse.from_dict(response.to_dict())
    assert restored.events["story_count"] == 1
    assert restored.trends[0]["source_count"] == 1


def test_non_news_response_has_no_story_view():
    response = SearchResponse.from_dict(_response(query="capital of Australia").to_dict())
    assert response.events == {}
    assert response.trends == []


# --------------------------------------------------------------------------- #
# Regressions for defects found while building the lanes
# --------------------------------------------------------------------------- #

def test_failed_sitemap_fetches_consume_the_budget(monkeypatch):
    """Counting only *successes* made the bound decorative.

    A publisher whose sitemaps all fail could otherwise be walked one request
    at a time through an arbitrarily long index.
    """
    index = "".join(f"<sitemap><loc>https://bbc.com/m{i}.xml</loc></sitemap>"
                    for i in range(200))
    table = {"https://bbc.com/robots.txt": "",
             "https://bbc.com/sitemap.xml": f"<sitemapindex>{index}</sitemapindex>"}
    backend, seen = _sitemap_backend(monkeypatch, table)
    with pytest.raises(SourceUnavailableError):
        backend.search("climate", SearchFilters.build(include_domains=["bbc.com"]),
                       5, Context())
    assert len(seen) <= MAX_SITEMAP_FETCHES + 1


def test_sitemap_human_verification_stays_typed(monkeypatch):
    """A wall must not be flattened into 'the archive is empty'."""
    backend = SitemapsBackend()

    def wall(url, timeout):
        raise VerificationRequiredError(
            "the site asked for human verification instead of serving the page",
            hint="reported, not bypassed", detail="challenge:captcha", url=url)

    monkeypatch.setattr(backend, "_fetch", wall)
    with pytest.raises(VerificationRequiredError):
        backend.search("climate policy in 2019",
                       SearchFilters.build(include_domains=["bbc.com"]), 5, Context())


def test_containment_is_not_reported_as_exact_similarity():
    # One headline contains the other, but they are not the same set of words.
    assert _story_similarity("Assam floods displace thousands",
                             "Assam floods displace thousands across state") < 1.0


# --------------------------------------------------------------------------- #
# Engine integration: archive ranking and the story-level view
# --------------------------------------------------------------------------- #

def _days_ago(n):
    """A publication date relative to the real clock.

    The orchestrator reads the wall clock, so a hard-coded date in an engine
    test becomes a time bomb: the news window slides past it and the test
    starts failing on an unrelated day.
    """
    from datetime import date as _d, timedelta as _t
    return (_d.today() - _t(days=n)).isoformat()


def _engine(tmp_path, monkeypatch, rows, **settings_kw):
    from iralens.cache import ResponseCache
    from iralens.reliability import ConcurrencyGate
    from iralens.search import SearchEngine
    from iralens.search.engines.base import SearchBackend
    from iralens.settings import Settings

    class Table(SearchBackend):
        name = "bing-news"

        def search(self, query, filters, limit, context, *, offset=0):
            return [SearchHit(title=r["title"], url=r["url"], snippet=r.get("snippet", ""),
                              engine=self.name, position=i + 1, query=query,
                              published_at=r.get("published_at"),
                              date_kind=r.get("date_kind", ""),
                              raw=r.get("raw", {})) for i, r in enumerate(rows)]

    settings = Settings(search_engines=("bing-news",), search_min_engines=1,
                        news_verify_dates=False, **settings_kw)
    return SearchEngine(settings, backends={"bing-news": Table()},
                        cache=ResponseCache(tmp_path / "c"),
                        gate=ConcurrencyGate(1, 1.0))


def test_archive_article_is_not_penalised_for_being_old(tmp_path, monkeypatch):
    """For 'in 2019', a 2019 article is the correct answer, not a stale one."""
    from iralens.search.engine import SearchEngine as _SE  # noqa: F401
    rows = [{"title": "Climate policy accord signed after long talks",
             "url": "https://publisher.test/2019/03/climate-policy-accord",
             "published_at": "2019-03-14", "date_kind": "publication"}]
    eng = _engine(tmp_path, monkeypatch, rows)
    resp = eng.search("climate policy accord in 2019", context=Context())
    assert resp.news.get("is_archive") is True
    assert resp.results, "the archive window must not filter out the right answer"
    breakdown = resp.results[0].score_breakdown
    assert breakdown["freshness"] == pytest.approx(0.5, abs=0.2), breakdown


def test_recent_news_still_decays_with_age(tmp_path, monkeypatch):
    rows = [{"title": "Assam floods displace thousands in Barpeta district",
             "url": "https://publisher.test/2019/floods",
             "published_at": "2019-03-14", "date_kind": "publication"}]
    eng = _engine(tmp_path, monkeypatch, rows)
    resp = eng.search("latest Assam floods news", context=Context())
    assert resp.news.get("is_archive") is False
    if resp.results:
        assert resp.results[0].score_breakdown["freshness"] < 0.2


def test_news_response_carries_a_story_view(tmp_path, monkeypatch):
    rows = [
        {"title": "Floods hit Assam, thousands displaced",
         "url": "https://assamtribune.com/a", "published_at": _days_ago(2),
         "date_kind": "publication"},
        {"title": "Assam floods displace thousands",
         "url": "https://sentinelassam.com/b", "published_at": _days_ago(1),
         "date_kind": "publication"},
        {"title": "Assam floods: thousands displaced",
         "url": "https://thehindu.com/c", "published_at": _days_ago(0),
         "date_kind": "publication"},
    ]
    eng = _engine(tmp_path, monkeypatch, rows)
    resp = eng.search("Assam floods latest news", context=Context())
    assert resp.events.get("story_count", 0) >= 1
    assert resp.events["story_count"] <= len(resp.results)
    assert resp.trends
    assert resp.trends[0]["evidence"]


def test_story_view_can_be_disabled(tmp_path, monkeypatch):
    rows = [{"title": "Assam floods displace thousands",
             "url": "https://assamtribune.com/a", "published_at": _days_ago(1),
             "date_kind": "publication"}]
    eng = _engine(tmp_path, monkeypatch, rows, news_events_enabled=False)
    resp = eng.search("Assam floods latest news", context=Context())
    assert resp.events == {}
    assert resp.trends == []


def test_non_news_query_gets_no_story_view(tmp_path, monkeypatch):
    rows = [{"title": "Canberra is the capital of Australia",
             "url": "https://publisher.test/capital"}]
    eng = _engine(tmp_path, monkeypatch, rows)
    resp = eng.search("capital of Australia", context=Context())
    assert resp.events == {}
    assert resp.trends == []
    assert resp.news == {}


def test_story_functions_do_not_read_the_wall_clock(monkeypatch):
    """Guard against the time bomb this suite already hit once.

    A trend window computed from the real 'today' silently drops fixtures as
    the calendar moves, so a green test turns red on an unrelated day. Every
    story-level entry point must accept an injected clock.
    """
    import iralens.search.events as events_mod

    def explode():
        raise AssertionError("the wall clock was read despite an injected today")

    results = [
        _ranked("Floods hit Assam, thousands displaced", "https://assamtribune.com/a", "2026-11-02"),
        _ranked("Assam floods displace thousands", "https://sentinelassam.com/b", "2026-11-03"),
        _ranked("Assam floods: thousands displaced", "https://thehindu.com/c", "2026-11-04"),
    ]
    monkeypatch.setattr(events_mod, "_today", explode)
    clusters = cluster_results(results, today=TODAY)
    assert max(c.source_count for c in clusters) == 3
    timeline = build_timeline("Assam floods", results, today=TODAY)
    assert timeline.first_report == "2026-11-02"
    signals = detect_trends("Assam floods", results, today=TODAY)
    assert signals[0].source_count == 3
