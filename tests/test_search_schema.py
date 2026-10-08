"""Schema validation: filters, options, domain normalization, round trips."""

import pytest

from halfiralens.search.schema import (
    EngineOutcome, FilterError, RankedResult, SearchFilters, SearchOptions, SearchResponse, normalize_domain,
)


def test_normalize_domain_strips_scheme_www_path_and_case():
    assert normalize_domain("https://WWW.Example.com/a/b?x=1") == "example.com"
    assert normalize_domain("news.bbc.co.uk") == "news.bbc.co.uk"


def test_build_filters_normalizes_and_sorts():
    f = SearchFilters.build(include_domains=["https://b.org", "a.com", "b.org"], file_type=".PDF")
    assert f.include_domains == ("a.com", "b.org")
    assert f.file_type == "pdf"
    assert not f.is_empty()


def test_empty_filters_report_empty():
    assert SearchFilters.build().is_empty()


@pytest.mark.parametrize("kwargs, message", [
    ({"date_from": "2025/01/01"}, "yyyy-mm-dd"),
    ({"date_to": "2025-02-30"}, "not a real date"),
    ({"date_from": "2025-05-01", "date_to": "2025-01-01"}, "must not be after"),
    ({"file_type": "pdf; rm -rf"}, "bare extension"),
])
def test_invalid_filters_raise_with_actionable_message(kwargs, message):
    with pytest.raises(FilterError, match=message):
        SearchFilters.build(**kwargs)


def test_options_validation():
    with pytest.raises(FilterError):
        SearchOptions(cache="sometimes")
    with pytest.raises(FilterError):
        SearchOptions(max_results=0)
    assert SearchOptions().cache == "use"


def test_response_round_trip_and_degraded_flag():
    result = RankedResult(
        title="t", url="https://a.test", canonical_url="https://a.test", snippet="s", score=0.5,
        score_breakdown={"relevance": 1.0}, engines=["duckduckgo"], engine_ranks={"duckduckgo": 1},
        published_at=None, date_status="unknown", queries=["q"], security_flags=[],
    )
    resp = SearchResponse(
        query="q", results=[result], queries=[{"text": "q", "strategy": "original"}],
        outcomes=[],
        fallbacks=[], filters={}, cache={}, dedup_log=[], no_results_reason="none",
    )
    resp.outcomes = [EngineOutcome(engine="bing", status="failed", kind="captcha")]
    data = resp.to_dict()
    assert data["degraded"] is True
    back = SearchResponse.from_dict(data)
    assert back.results[0].url == "https://a.test"
    assert back.outcomes[0].kind == "captcha"
