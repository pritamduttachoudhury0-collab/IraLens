# -*- coding: utf-8 -*-
"""Offline tests for wrapper resolution and aggregator demotion.

Every test here is a pure function of a captured fixture: no network, no
clock, no real cache. That is deliberate — the failure this engine fixes was
found *because* the reader reported success on a URL it never actually
resolved, so the tests must not be able to pass by reaching the live provider.

The captured fixtures are real bytes kept in ``tests/fixtures/gn``:
``interstitial.html`` is the redirect page Google served on 2026-11-16 (only
the signature-bearing tail is retained, 1.4 KB of ~601 KB), and
``batchexecute.txt`` is the endpoint's reply to the request the resolver
builds from it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from iralens.search.engines import url_resolve as R
from iralens.search.engines.aggregators import (aggregator_host, is_aggregator,
                                                is_wrapper)
from iralens.search.engines.news_rss import (GoogleNewsBackend, decode_bing_apiclick,
                                             decode_google_news_url, parse_news_rss)

FIXTURES = Path(__file__).parent / "fixtures" / "gn"

ARTIFACT_ID = (
    "CBMiggFBVV95cUxQZ0pVd0VKNnk2Vmpxalp6amtSaFlvX0Vpd0NOQkNZVldpTjRY"
    "dzQyZHd2c2IwdmhEdm5RNElZWXJ6bWtMVGhlemhGX0FqM0d3Vnp3MWRvWUNxTGJw"
    "MnV3Ymx0cy1TbWJJMVliVi1HM0xaUExCZ0FQU3NOMEJWWHJhaGZ3"
)
BLOB_URL = f"https://news.google.com/rss/articles/{ARTIFACT_ID}?oc=5"
TARGET = "https://thehackernews.com/2026/11/stolen-fireworks-ai-key-led-to-cursor.html"


class _StubCache:
    """A ResponseCache lookalike with an inspectable store."""

    def __init__(self):
        self.store = {}

    def get(self, namespace, payload, ttl_seconds):
        # Mirrors ResponseCache.get: (value, age_seconds) or None.
        value = self.store.get((namespace, payload["url"]))
        return (value, 0.0) if value is not None else None

    def put(self, namespace, payload, value, cacheable=True):
        self.store[(namespace, payload["url"])] = value


def _resolver(**kw):
    kw.setdefault("cache", _StubCache())
    return R.GoogleNewsResolver(**kw)


# --------------------------------------------------------------------------- #
# The request the resolver builds (this is the bug that cost 15/15 in dev:
# one extra positional `null` made the endpoint answer `null`, code 3).
# --------------------------------------------------------------------------- #

def test_garturlreq_has_exactly_twelve_positional_args():
    filled = R._GARTURLREQ % ("ID", 1794848640, "SIG")
    req = json.loads(filled)
    assert req[0] == "garturlreq"
    assert len(req[1]) == 12
    # The three pieces Google's own client varies must land in the right slots.
    assert req[2] == "ID"
    assert req[3] == 1794848640
    assert req[4] == "SIG"
    # A regression guard: the client-state block has a fixed width.
    assert len(req[1][0]) == 17


# --------------------------------------------------------------------------- #
# Signature extraction from the intercepted redirect page
# --------------------------------------------------------------------------- #

def test_signature_is_read_from_the_tail_of_the_interstitial():
    html = (FIXTURES / "interstitial.html").read_text(encoding="utf-8")
    assert R._SIG_ID.search(html).group(1) == ARTIFACT_ID
    assert R._SIG_TS.search(html).group(1) == "1794848640"
    assert R._SIG_SG.search(html).group(1) == "AdwtSYO6h0TnjaATR4OsEj58TtIz"


def test_target_is_parsed_out_of_the_batchexecute_envelope():
    reply = (FIXTURES / "batchexecute.txt").read_text(encoding="utf-8")
    assert R._TARGET.search(reply).group(1) == TARGET


# --------------------------------------------------------------------------- #
# Resolution, end to end, with the network replaced by fixtures
# --------------------------------------------------------------------------- #

def test_resolver_recovers_the_publisher_url():
    html = (FIXTURES / "interstitial.html").read_text(encoding="utf-8")
    reply = (FIXTURES / "batchexecute.txt").read_text(encoding="utf-8")
    r = _resolver(transport_get=lambda url, timeout: html,
                  transport_post=lambda url, body, timeout: reply)
    res = r.resolve(BLOB_URL)
    assert res.status == R.STATUS_RESOLVED
    assert res.url == TARGET
    assert res.ok and res.usable


def test_resolver_request_is_what_the_endpoint_expects():
    """The POST body is urlencoded `f.req` holding a JSON-in-JSON envelope."""
    captured = {}

    def post(url, body, timeout):
        captured["url"], captured["body"] = url, body
        return (FIXTURES / "batchexecute.txt").read_text(encoding="utf-8")

    r = _resolver(transport_get=lambda url, timeout: (FIXTURES / "interstitial.html").read_text(encoding="utf-8"),
                  transport_post=post)
    r.resolve(BLOB_URL)
    assert captured["url"] == R._BATCHEXECUTE
    from urllib.parse import parse_qs
    outer = parse_qs(captured["body"].decode())["f.req"][0]
    inner = json.loads(outer)[0][0]
    assert inner[0] == "Fbv4je"
    assert json.loads(inner[1])[0] == "garturlreq"


def test_successful_resolution_is_cached_and_the_second_call_costs_nothing():
    html = (FIXTURES / "interstitial.html").read_text(encoding="utf-8")
    reply = (FIXTURES / "batchexecute.txt").read_text(encoding="utf-8")
    calls = {"get": 0}

    def get(url, timeout):
        calls["get"] += 1
        return html

    cache = _StubCache()
    r = _resolver(cache=cache, transport_get=get,
                  transport_post=lambda url, body, timeout: reply)
    first = r.resolve(BLOB_URL)
    second = r.resolve(BLOB_URL)
    assert first.status == R.STATUS_RESOLVED
    assert second.status == R.STATUS_CACHED and second.url == TARGET
    assert calls["get"] == 1, "a cached id must not hit the network again"


def test_a_consent_wall_is_reported_not_guessed():
    """No signature -> typed failure with the blob URL, never a fabricated one."""
    r = _resolver(transport_get=lambda url, timeout: "<html>consent</html>",
                  transport_post=lambda *a, **k: pytest.fail("must not POST"))
    res = r.resolve(BLOB_URL)
    assert res.status == R.STATUS_NO_SIGNATURE
    assert res.url == BLOB_URL, "an unresolved wrapper keeps its own URL"
    assert not res.ok and not res.usable


def test_a_null_reply_is_reported_not_guessed():
    r = _resolver(
        transport_get=lambda url, timeout: (FIXTURES / "interstitial.html").read_text(encoding="utf-8"),
        transport_post=lambda url, body, timeout: '[["wrb.fr","Fbv4je",null,null,null,[3],"generic"]]')
    res = r.resolve(BLOB_URL)
    assert res.status == R.STATUS_NO_TARGET
    assert res.url == BLOB_URL


def test_transport_errors_never_escape_a_search(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("upstream slow")

    r = _resolver(transport_get=boom)
    res = r.resolve(BLOB_URL)
    assert res.status == R.STATUS_FETCH_FAILED
    assert res.url == BLOB_URL and not res.ok

    from io import BytesIO
    from urllib.error import HTTPError

    from iralens import fetch

    def forbidden(url, **kwargs):
        raise HTTPError(url, 403, "Forbidden", {}, None)

    def captcha(url, **kwargs):
        # Even if a challenge also includes signature markup, do not POST.
        signature = (FIXTURES / "interstitial.html").read_bytes()
        return BytesIO(b'<html><div class="g-recaptcha"></div>' + signature + b'</html>')

    for upstream in (forbidden, captcha):
        monkeypatch.setattr(fetch, "safe_urlopen", upstream)
        resolver = _resolver(transport_post=lambda *a, **k: pytest.fail("must not POST"))
        assert resolver.resolve(BLOB_URL).status == R.STATUS_FETCH_FAILED


def test_budget_is_enforced_and_reported():
    r = _resolver(budget=1,
                  transport_get=lambda url, timeout: "<html>consent</html>")
    first = r.resolve(BLOB_URL)
    second = r.resolve(BLOB_URL.replace("oc=5", "oc=6"))
    assert first.status == R.STATUS_NO_SIGNATURE
    assert second.status == R.STATUS_BUDGET_EXHAUSTED


def test_disabled_resolution_says_so():
    res = _resolver(enabled=False).resolve(BLOB_URL)
    assert res.status == R.STATUS_DISABLED and res.url == BLOB_URL


def test_non_wrapper_urls_pass_through_untouched():
    cache = _StubCache()
    r = _resolver(cache=cache, transport_get=lambda *a, **k: pytest.fail("no network"))
    for url in ("https://www.reuters.com/world/x",
                "https://news.google.com/",
                "https://news.google.com/rss/search?q=ai",
                "https://notnews.google.com/rss/articles/X"):
        res = r.resolve(url)
        assert res.status == R.STATUS_NOT_WRAPPED
        assert res.url == url and res.usable
    assert not cache.store


# --------------------------------------------------------------------------- #
# Offline decoding: only the legacy form, and never a guess
# --------------------------------------------------------------------------- #

def test_legacy_token_decodes_offline():
    import base64
    target = "https://www.reuters.com/world/example-2026-11-16/"
    raw = b"\x08\x13" + target.encode() + b"\x00\x01"
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    url = f"https://news.google.com/rss/articles/{token}"
    assert decode_google_news_url(url) == target


def test_modern_token_is_not_guessed_at():
    """An encrypted token must come back unchanged, not as a plausible URL."""
    assert decode_google_news_url(BLOB_URL) == BLOB_URL


def test_bing_wrapper_is_offline_and_exact():
    url = ("https://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&"
           "url=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&c=1")
    assert decode_bing_apiclick(url) == "https://example.com/a?b=1"
    # A URL that merely mentions bing.com is left alone.
    assert decode_bing_apiclick("https://example.com/?u=https://bing.com") == \
        "https://example.com/?u=https://bing.com"


# --------------------------------------------------------------------------- #
# Aggregators: portals are demoted, wrappers are droppable, publishers are not
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url,expected", [
    ("https://news.google.com/rss/articles/X", "news.google.com"),
    ("https://www.msn.com/en-us/news/technology/x/ar-AA1", "msn.com"),
    ("https://news.yahoo.com/some-story-123.html", "news.yahoo.com"),
    ("https://apple.news/Aabc123", "apple.news"),
    ("https://www.reuters.com/world/x", None),
    ("https://finance.yahoo.com/quote/NVDA/", "yahoo.com"),
    ("https://notmsn.com/x", None),
    ("", None),
])
def test_aggregator_detection(url, expected):
    assert aggregator_host(url) == expected
    assert is_aggregator(url) is (expected is not None)


def test_only_wrappers_are_droppable():
    assert is_wrapper("https://news.google.com/rss/articles/X")
    assert is_wrapper("https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fa.com")
    # A portal landing page is real content: demoted, never dropped.
    assert not is_wrapper("https://www.msn.com/en-us/news/technology/x/ar-AA1")
    assert not is_wrapper("https://news.google.com/")


# --------------------------------------------------------------------------- #
# Feed parsing marks unresolved wrappers instead of hiding them
# --------------------------------------------------------------------------- #

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Resolved story</title>
  <link>https://news.google.com/rss/articles/{ok}</link>
  <description>one</description><pubDate>Sun, 16 Nov 2026 06:00:00 GMT</pubDate>
  <source>Reuters</source></item>
<item><title>Opaque story</title>
  <link>https://news.google.com/rss/articles/{bad}</link>
  <description>two</description><pubDate>Sun, 16 Nov 2026 07:00:00 GMT</pubDate>
  <source>AP</source></item>
</channel></rss>"""


def _feed():
    return RSS.format(ok="OKTOKEN", bad="BADTOKEN")


def test_unresolved_wrapper_is_marked_not_silently_dropped_or_reported_ok():
    def unwrap(link):
        return ("https://www.reuters.com/a" , "resolved") if "OKTOKEN" in link \
            else (link, "no_signature")

    log = {}
    hits = parse_news_rss(_feed(), "q", 10, engine="google-news",
                          unwrap=unwrap, resolution_log=log)
    assert len(hits) == 2, "the parser must not decide policy; it must report"
    by_title = {h.title: h for h in hits}
    assert by_title["Resolved story"].url == "https://www.reuters.com/a"
    opaque = by_title["Opaque story"]
    assert opaque.raw["wrapper_unresolved"] is True
    assert opaque.raw["wrapper_status"] == "no_signature"
    assert "wrapper_unresolved" not in by_title["Resolved story"].raw
    assert log == {"resolved": 1, "no_signature": 1}


def test_plain_url_unwrap_keeps_the_historical_contract():
    hits = parse_news_rss(_feed(), "q", 10, engine="google-news",
                          unwrap=lambda link: link.replace("OKTOKEN", "x"))
    assert all("wrapper_unresolved" not in h.raw for h in hits)


def test_google_backend_prefers_offline_decode_then_reports_status(monkeypatch):
    """The backend's unwrap returns (url, status) and never invents a target."""
    backend = GoogleNewsBackend()

    class _R:
        def resolve(self, url):
            return R.Resolution(R.STATUS_NO_SIGNATURE, url, "no sig", 1)

    monkeypatch.setattr("iralens.search.engines.news_rss._default_resolver",
                        lambda settings=None: _R())
    url, status = backend.unwrap(BLOB_URL)
    assert url == BLOB_URL and status == R.STATUS_NO_SIGNATURE


def test_google_backend_returns_resolved_publisher_url(monkeypatch):
    backend = GoogleNewsBackend()

    class _R:
        def resolve(self, url):
            return R.Resolution(R.STATUS_RESOLVED, TARGET, "", 2)

    monkeypatch.setattr("iralens.search.engines.news_rss._default_resolver",
                        lambda settings=None: _R())
    url, status = backend.unwrap(BLOB_URL)
    assert (url, status) == (TARGET, R.STATUS_RESOLVED)


# --------------------------------------------------------------------------- #
# Orchestrator behaviour: drop unresolved, demote portals, keep publishers
# --------------------------------------------------------------------------- #

def _engine(tmp_path, hits, monkeypatch, **settings_kw):
    from iralens.cache import ResponseCache
    from iralens.reliability import ConcurrencyGate
    from iralens.search import SearchEngine
    from iralens.search.engines.base import SearchBackend
    from iralens.search.schema import SearchHit
    from iralens.settings import Settings

    class Table(SearchBackend):
        name = "google-news"

        def search(self, query, filters, limit, context, *, offset=0):
            return [SearchHit(title=r["title"], url=r["url"], snippet=r.get("snippet", ""),
                              engine=self.name, position=i + 1, query=query,
                              published_at=r.get("published_at"),
                              date_kind="publication", raw=dict(r.get("raw", {})))
                    for i, r in enumerate(hits)]

    settings = Settings(search_engines=("google-news",), search_min_engines=1,
                        news_verify_dates=False, **settings_kw)
    return SearchEngine(settings, backends={"google-news": Table()},
                        cache=ResponseCache(tmp_path / "c"),
                        gate=ConcurrencyGate(1, 1.0))


def _stub_context(monkeypatch):
    from iralens.config import Config

    class Context:
        config = Config(read_only=True)

    return Context()


def test_opaque_wrapper_is_dropped_and_counted(tmp_path, monkeypatch):
    hits = [
        {"title": "A real publisher story about rates",
         "url": "https://www.reuters.com/business/rates/",
         "published_at": "2026-11-15", "raw": {}},
        {"title": "A story we could not unwrap about rates",
         "url": BLOB_URL, "published_at": "2026-11-16",
         "raw": {"wrapper_unresolved": True, "wrapper_status": "no_signature"}},
    ]
    eng = _engine(tmp_path, hits, monkeypatch)
    resp = eng.search("Federal Reserve interest rate decision news", context=_stub_context(monkeypatch))
    urls = [r.url for r in resp.results]
    assert BLOB_URL not in urls, "an unresolvable wrapper must never be a result"
    assert "https://www.reuters.com/business/rates/" in urls
    assert resp.filters["report"]["unresolved_wrappers_dropped"] == {"no_signature": 1}

    # The ablation may keep disabled wrappers, but not a known no-target result.
    from iralens.search.schema import SearchOptions

    hits[1]["raw"]["wrapper_status"] = "no_target"
    eng = _engine(tmp_path, hits, monkeypatch, news_drop_unresolved_wrappers=False,
                  news_aggregator_policy="off")
    resp = eng.search("Federal Reserve interest rate decision news", options=SearchOptions(cache="bypass"),
                      context=_stub_context(monkeypatch))
    assert all(r.url != BLOB_URL for r in resp.results)
    assert resp.filters["report"]["unresolved_wrappers_dropped"] == {"no_target": 1}


def _portal_case():
    """One portal copy and one publisher copy of the same on-topic story."""
    return [
        # Without intervention the msn URL wins: it names the entity exactly
        # and the Reuters permalink does not.
        {"title": "Federal Reserve holds interest rates steady as decision nears",
         "url": "https://www.msn.com/en-us/news/money/fed-holds/ar-AA1",
         "published_at": "2026-11-16", "raw": {}},
        {"title": "Interest rate decision: Fed holds steady in November",
         "url": "https://www.reuters.com/business/fed-holds/",
         "published_at": "2026-11-16", "raw": {}},
    ]


def test_portal_is_dropped_by_default(tmp_path, monkeypatch):
    """Default policy: a portal slot is a slot the caller cannot read.

    Measured 2026-11-16: msn returns a 10-character "Title: MSN" success and
    yahoo a typed fetch error. Neither is a source, so neither is a result.
    """
    eng = _engine(tmp_path, _portal_case(), monkeypatch)
    resp = eng.search("Federal Reserve interest rate decision news",
                      context=_stub_context(monkeypatch))
    urls = [r.url for r in resp.results]
    assert not any("msn.com" in u for u in urls), "an unreadable portal is not a result"
    assert urls and urls[0].startswith("https://www.reuters.com/")

    evergreen = eng.search("Federal Reserve interest rates", context=_stub_context(monkeypatch))
    assert any("msn.com" in r.url for r in evergreen.results)

    from iralens.sources.web import _empty_body_reason

    assert _empty_body_reason("Title: MSN", "https://www.msn.com/x") == "portal_shell"
    assert not _empty_body_reason("Title: MSN", "https://example.org/x")
    assert not _empty_body_reason("Title: MSN\n\nOne-line article.", "https://www.msn.com/x")


def test_portal_is_demoted_when_policy_says_so(tmp_path, monkeypatch):
    """`demote` keeps the portal (it may be readable for an operator) but
    ranks every publisher above it, and labels why."""
    eng = _engine(tmp_path, _portal_case(), monkeypatch,
                  news_aggregator_policy="demote")
    resp = eng.search("Federal Reserve interest rate decision news",
                      context=_stub_context(monkeypatch))
    urls = [r.url for r in resp.results]
    assert any("msn.com" in u for u in urls), "demote must not drop"
    assert urls[0].startswith("https://www.reuters.com/"), "publisher outranks portal"
    demoted = [r for r in resp.results if "msn.com" in r.url]
    assert 0 < demoted[0].score_breakdown["aggregator_demotion_multiplier"] < 1


def test_policy_off_leaves_ranking_alone(tmp_path, monkeypatch):
    eng = _engine(tmp_path, _portal_case(), monkeypatch,
                  news_aggregator_policy="off")
    resp = eng.search("Federal Reserve interest rate decision news",
                      context=_stub_context(monkeypatch))
    assert len(resp.results) == 2
    assert "aggregator_demotion_multiplier" not in resp.results[0].score_breakdown


def test_publisher_ranking_is_untouched_by_the_policy(tmp_path, monkeypatch):
    hits = [{"title": "Fed holds rates steady as inflation cools",
             "url": "https://www.reuters.com/business/fed-holds/",
             "published_at": "2026-11-16", "raw": {}}]
    eng = _engine(tmp_path, hits, monkeypatch)
    resp = eng.search("Federal Reserve interest rate decision news", context=_stub_context(monkeypatch))
    assert resp.results
    assert "aggregator_demotion_multiplier" not in resp.results[0].score_breakdown
