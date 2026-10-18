# -*- coding: utf-8 -*-
"""Phase B — Completeness & Scale.

One test per defect in the error register (E-14 … E-27), each named after
the register entry so the two can be read side by side.

All offline: the network transports are fakes.
"""

import urllib.error

import pytest

from iralens.errors import SourceUnavailableError, classify_http_status
from iralens.search.schema import FilterError
from iralens.model import PostRecord
from iralens.reliability import HostRateLimiter
from iralens.search.coverage import assess_coverage, question_terms
from iralens.search.engines.base import SearchBackend
from iralens.search.querytext import parse_site_operator, site_scoped_query, tokenize
from iralens.search.schema import SearchFilters, SearchHit, SearchOptions
from iralens.sources.indiehackers import (
    IndieHackersSource,
    _first_article,
    _jsonld_blocks,
    extract_post_records,
)
from iralens.sources.reddit import RedditSource, _normalize_subreddit


# ----------------------------------------------------------------- helpers
class _Resp:
    def __init__(self, body: bytes, url: str = "https://example.org/x",
                 status: int = 200, headers=None):
        import io
        self._buf = io.BytesIO(body)
        self.url = url
        self.status = status
        self.headers = headers or {"Content-Type": "text/html"}

    def read(self, n=-1):
        return self._buf.read(n if n >= 0 else len(self._buf.getvalue()))

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _hit(url, title="t", snippet="s", published=None, engine="fake"):
    return SearchHit(title=title, url=url, snippet=snippet, engine=engine,
                     position=1, published_at=published)


# ------------------------------------------------------------------- E-14
def test_E14_site_operator_survives_the_tokenizer():
    """The `site:` operator must not be shredded into bare words."""
    assert "site:reddit.com" in site_scoped_query("site:reddit.com/r/SaaS pricing")
    tokens = tokenize("site:reddit.com/r/SaaS pricing")
    assert "reddit.com" in tokens
    assert "site" not in tokens
    # The scope is parsed out of the query, with the host preserved.
    domains, cleaned = parse_site_operator("site:reddit.com/r/SaaS pricing")
    assert domains == ["reddit.com"]
    assert "reddit.com" not in cleaned
    assert "pricing" in cleaned


def test_E14_reformulation_keeps_the_site_scope():
    from iralens.search.reformulate import _keywords

    keywords = _keywords("site:reddit.com/r/SaaS pricing")
    assert "pricing" in keywords
    assert any("site:reddit.com" in k for k in keywords)


# ------------------------------------------------------------------- E-15
def test_E15_site_scope_is_enforced_on_the_results():
    """A scoped query must not return off-scope results."""
    from iralens.search.engine import SearchEngine
    from iralens.settings import Settings

    class _Backend(SearchBackend):
        name = "fake"
        handled = {}

        def search(self, query, filters, limit, context, *, offset=0):
            return [
                _hit("https://www.reddit.com/r/SaaS/pricing", title="pricing"),
                _hit("https://example.com/pricing", title="pricing"),
            ]

    settings = Settings(search_engines=("fake",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0)
    engine = SearchEngine(settings, backends={"fake": _Backend()})
    response = engine.search("site:reddit.com/r/SaaS pricing",
                             options=SearchOptions(reformulate=False))
    urls = [r.url for r in response.results]
    assert urls, "the in-scope result must survive"
    assert all("reddit.com" in u for u in urls), urls


# ------------------------------------------------------------------- E-16
def test_E16_backends_accept_an_offset_and_the_paginating_ones_send_it():
    from iralens.search.engines import build_backends

    backends = build_backends()
    assert backends["bing"].paginated is True
    assert backends["duckduckgo"].paginated is True
    assert backends["wikipedia"].paginated is True
    for backend in backends.values():
        import inspect

        sig = inspect.signature(backend.search)
        assert "offset" in sig.parameters, backend.name


def test_E16_pagination_changes_the_request_url():
    from iralens.search.engines.bing import BingBackend
    from iralens.search.engines.duckduckgo import DuckDuckGoBackend

    captured = []

    class _Ctx:
        config = {"search_timeout_seconds": 5}

        def session(self):
            return self

    import iralens.search.engines.bing as bing_mod
    import iralens.search.engines.duckduckgo as ddg_mod

    def _fake_fetch(context, url, timeout, **kwargs):
        captured.append(url)
        return "<html><body>no results</body></html>"

    bing_mod.fetch_serp_html = _fake_fetch
    ddg_mod.fetch_serp_html = _fake_fetch
    with pytest.raises(SourceUnavailableError):
        BingBackend().search("q", SearchFilters(), 5, _Ctx(), offset=10)
    assert "first=11" in captured[-1], captured[-1]
    try:
        DuckDuckGoBackend().search("q", SearchFilters(), 5, _Ctx(), offset=10)
    except Exception:
        pass
    assert "s=10" in captured[-1], captured[-1]


def test_E16_search_options_carry_an_offset():
    options = SearchOptions(offset=25)
    assert options.offset == 25
    with pytest.raises(FilterError):
        SearchOptions(offset=-1)


# ------------------------------------------------------------------- E-17
def test_E17_reddit_can_list_a_subreddit():
    source = RedditSource()
    assert "listing" in source.operations
    assert callable(source.list_subreddit)


def test_E17_subreddit_names_are_normalized():
    assert _normalize_subreddit("SaaS") == "SaaS"
    assert _normalize_subreddit("r/SaaS") == "SaaS"
    assert _normalize_subreddit("/r/SaaS/") == "SaaS"
    assert _normalize_subreddit("https://www.reddit.com/r/SaaS/") == "SaaS"


# ------------------------------------------------------------------- E-18
def test_E18_the_anonymous_rss_transport_is_wired_in():
    source = RedditSource()
    assert "rss" in source.backends
    assert hasattr(source, "_listing_via_rss")


# ------------------------------------------------------------------- E-19
def test_E19_an_indiehackers_source_exists():
    from iralens.sources import get_source, source_names

    assert "indiehackers" in source_names()
    source = get_source("indiehackers")
    assert source is not None
    assert source.can_handle("https://www.indiehackers.com/post/some-post-abc123")
    assert "posts" in source.operations and "products" in source.operations


def test_E19_indiehackers_reads_json_ld():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"BlogPosting","headline":"My launch","articleBody":"The body text.",
     "datePublished":"2026-01-02T03:04:05Z",
     "author":{"@type":"Person","name":"Ada"},
     "interactionStatistic":[
        {"@type":"InteractionCounter","interactionType":"https://schema.org/LikeAction",
         "userInteractionCount":42},
        {"@type":"InteractionCounter","interactionType":"https://schema.org/CommentAction",
         "userInteractionCount":7}]}
    </script></head><body></body></html>
    """
    blocks = _jsonld_blocks(html)
    article = _first_article(blocks)
    assert article.get("headline") == "My launch"
    source = IndieHackersSource()
    artifact = source._post_artifact(
        html, "https://www.indiehackers.com/post/x-abc12345", 200,
        "https://www.indiehackers.com/post/x-abc12345", "text/html")
    assert artifact is not None
    assert artifact.metadata["title"] == "My launch"
    assert artifact.metadata["score"] == 42
    assert artifact.metadata["comments"] == 7
    assert artifact.metadata["author"] == "Ada"


def test_E19_indiehackers_extracts_post_links():
    html = """
    <div><a href="https://www.indiehackers.com/post/launch-day-abc12345">Launch day</a>
    <a href="https://www.indiehackers.com/ada">ada</a> 28 upvotes 12 comments</div>
    """
    records = extract_post_records(html)
    assert records
    assert records[0].title == "Launch day"
    assert "abc12345" in records[0].url
    assert records[0].score == 28
    assert records[0].comments == 12


# ------------------------------------------------------------------- E-20
def test_E20_unknown_dates_can_be_excluded():
    from iralens.search.engine import SearchEngine
    from iralens.settings import Settings

    class _Backend(SearchBackend):
        name = "fake"
        handled = {}

        def search(self, query, filters, limit, context, *, offset=0):
            return [_hit("https://a.example/1", published="2026-01-01"),
                    _hit("https://a.example/2", published=None)]

    settings = Settings(search_engines=("fake",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0)
    engine = SearchEngine(settings, backends={"fake": _Backend()})
    kept = engine.search("solar", filters=SearchFilters(date_from="2026-01-01"),
                         options=SearchOptions(reformulate=False))
    assert len(kept.results) == 2, "unknown dates are kept by default"

    strict = engine.search("solar",
                            filters=SearchFilters(date_from="2026-01-01",
                                                  exclude_unknown_dates=True),
                            options=SearchOptions(reformulate=False))
    assert [r.url for r in strict.results] == ["https://a.example/1"]


# ------------------------------------------------------------------- E-21
def test_E21_the_host_rate_limiter_paces_and_honours_retry_after():
    limiter = HostRateLimiter(min_interval=0.05, default_min_interval=0.05)
    slept = []
    limiter.wait("example.org", sleep=slept.append)   # first call: no wait
    limiter.wait("example.org", sleep=slept.append)   # second call: paced
    assert slept and slept[0] >= 0.04, slept

    limiter.note_retry_after("example.org", 30.0)
    assert limiter.cooldown_remaining("example.org") > 25.0


def test_E21_a_429_is_rate_limited_not_a_service_error():
    from iralens.errors import RateLimitedError

    error = classify_http_status(429, layer="origin", retry_after=12.0)
    assert isinstance(error, RateLimitedError)
    assert error.retry_after == 12.0


def test_E21_retry_after_from_the_origin_is_recorded(monkeypatch):
    from iralens.sources import web as web_mod

    recorded = {}
    monkeypatch.setattr(web_mod.HOST_RATE_LIMITER, "note_retry_after",
                        lambda host, seconds: recorded.update({host: seconds}))
    error = urllib.error.HTTPError("https://example.org/x", 429, "Too Many Requests",
                                    {"Retry-After": "17"}, None)
    web_mod._note_retry_after(error, "example.org")
    assert recorded["example.org"] == 17.0


# ------------------------------------------------------------------- E-22
def test_E22_generic_filler_is_not_reported_as_a_clean_answer():
    from iralens.search.engine import SearchEngine
    from iralens.settings import Settings

    class _Backend(SearchBackend):
        name = "fake"
        handled = {}

        def search(self, query, filters, limit, context, *, offset=0):
            return [_hit("https://a.example/1", title="unrelated",
                         snippet="nothing to do with the question")]

    settings = Settings(search_engines=("fake",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0)
    engine = SearchEngine(settings, backends={"fake": _Backend()})
    response = engine.search("quantum chromodynamics lattice",
                             options=SearchOptions(reformulate=False))
    assert response.results, "there are results ..."
    assert response.no_results_reason == "low_coverage", \
        "... but they do not answer the question and must not say 'none'"
    assert response.coverage["status"] == "low"


def test_E22_coverage_is_computed_for_the_question():
    terms = question_terms("site:reddit.com/r/SaaS pricing")
    assert "reddit.com" not in terms
    assert "pricing" in terms
    verdict = assess_coverage("solar panel efficiency", [
        _hit("https://a.example/solar", title="solar panel efficiency guide")])
    assert verdict["status"] == "good"
    empty = assess_coverage("solar panel efficiency", [])
    assert empty["status"] == "low"


# ------------------------------------------------------------------- E-23
def test_E23_an_unconfigured_engine_is_not_in_the_effective_chain():
    import shutil

    from iralens.search.engines import build_backends
    from iralens.search.engine import SearchEngine
    from iralens.settings import Settings

    engine = SearchEngine(Settings(), backends=build_backends())
    chain, readiness = engine.resolve_chain_with_readiness()
    assert "semantic-search" in readiness
    if shutil.which("mcporter"):
        assert "semantic-search" in chain
    else:
        assert "semantic-search" not in chain
        assert readiness["semantic-search"]["available"] is False
        # Not configured is not a failure: the engine never ran.
        assert "reason" in readiness["semantic-search"]


def test_E23_the_readiness_travels_with_the_response():
    from iralens.search.engines import build_backends
    from iralens.search.engine import SearchEngine
    from iralens.settings import Settings

    engine = SearchEngine(Settings(search_engines=("wikipedia",), search_min_engines=1),
                          backends=build_backends())
    response = engine.search("python", options=SearchOptions(reformulate=False))
    assert "wikipedia" in response.engine_readiness
    assert response.engine_readiness["wikipedia"]["available"] is True


# ------------------------------------------------------------------- E-24
def test_E24_provenance_is_not_taken_from_the_stale_last_query():
    from iralens import IraLens

    hil = IraLens()
    hil.session.last_query = "a completely unrelated question"
    hil.session.record_discovery("https://example.org/page", title="Page",
                                 source="web-search", found_via="search:the real query")
    artifact = hil.open.__self__  # keep the facade alive
    assert artifact is not None
    found = hil.session.found_via_for("https://example.org/page")
    assert found == "search:the real query"
    assert found != "search:a completely unrelated question"


def test_E24_an_unseen_url_is_marked_as_opened():
    from iralens import IraLens

    hil = IraLens()
    assert hil.session.found_via_for("https://example.org/never-seen") == ""


# ------------------------------------------------------------------- E-25
def test_E25_post_metadata_is_typed():
    record = PostRecord.from_mapping({
        "title": "A post", "permalink": "/r/SaaS/comments/abc/title",
        "author": "ada", "subreddit": "SaaS", "score": 12,
        "num_comments": 3, "created_at": "2026-01-01T00:00:00Z",
        "selftext": "the body", "flair": "question",
    }, source="reddit")
    assert record.title == "A post"
    assert record.author == "ada"
    assert record.subreddit == "SaaS"
    assert record.score == 12
    assert record.comments == 3
    assert record.created_at == "2026-01-01T00:00:00Z"
    assert record.body == "the body"
    # Nothing is lost: unknown fields survive in `extra`.
    assert record.extra["flair"] == "question"
    data = record.to_dict()
    assert data["score"] == 12 and data["comments"] == 3


# ------------------------------------------------------------------- E-26
def test_E26_urls_seen_in_an_earlier_query_are_excluded():
    from iralens.search.engine import SearchEngine
    from iralens.settings import Settings

    class _Backend(SearchBackend):
        name = "fake"
        handled = {}

        def search(self, query, filters, limit, context, *, offset=0):
            return [_hit("https://a.example/1"), _hit("https://a.example/2")]

    settings = Settings(search_engines=("fake",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0)
    engine = SearchEngine(settings, backends={"fake": _Backend()})
    first = engine.search("one", options=SearchOptions(reformulate=False))
    seen = [r.url for r in first.results]
    second = engine.search("two", options=SearchOptions(reformulate=False,
                                                        exclude_urls=tuple(seen)))
    assert second.results == []
    assert second.filters["report"].get("excluded_seen") == len(seen)


def test_E26_the_sweep_helper_deduplicates_across_queries(monkeypatch):
    from iralens import IraLens
    from iralens.search.engine import SearchEngine
    from iralens.settings import Settings
    from iralens.sources import get_source

    class _Backend(SearchBackend):
        name = "fake"
        handled = {}

        def search(self, query, filters, limit, context, *, offset=0):
            return [_hit("https://a.example/shared"), _hit(f"https://a.example/{query}")]

    settings = Settings(search_engines=("fake",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0)
    engine = SearchEngine(settings, backends={"fake": _Backend()})
    hil = IraLens()
    # The registry hands out module-level singletons: swapping the engine on
    # one *in place* leaks the fake into every later test in the session --
    # which is how a live test ended up opening https://a.example/shared.
    # monkeypatch restores it when the test ends.
    monkeypatch.setattr(get_source("web-search"), "_engine", engine)
    artifacts = hil.sweep(["alpha", "beta"], limit=5)
    urls = [a.url for a in artifacts]
    assert len(urls) == len(set(urls)), urls


# ------------------------------------------------------------------- E-27
def test_E27_rss_permalinks_are_canonical():
    from iralens.sources.rss import _canonical_entry_link

    warnings = []
    link = _canonical_entry_link({"link": "/posts/1"}, "https://site.example/blog",
                                 "https://site.example/feed.xml", warnings)
    assert link == "https://site.example/posts/1"
    assert warnings == []

    # A relative link resolves against the feed's own site root, not
    # against the feed document's path.
    link = _canonical_entry_link({"link": "posts/2"}, "https://site.example/blog/",
                                 "https://site.example/deep/feed.xml", warnings)
    assert link == "https://site.example/blog/posts/2"

    # A URL guid is used when the entry has no link.
    link = _canonical_entry_link({"id": "https://site.example/posts/3"}, "",
                                 "https://site.example/feed.xml", warnings)
    assert link == "https://site.example/posts/3"


def test_E27_an_entry_without_a_permalink_is_reported():
    from iralens.sources.rss import _canonical_entry_link

    warnings = []
    link = _canonical_entry_link({"title": "no link"}, "https://site.example/",
                                 "https://site.example/feed.xml", warnings)
    assert link == ""
    assert warnings and "permalink" in warnings[0]
