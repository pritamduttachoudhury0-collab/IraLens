"""Reformulation, URL canonicalization, dedup and ranking factors."""

from datetime import date

from iralens.search.dedup import dedup, title_similarity
from iralens.search.ranking import authority, combine, freshness, relevance
from iralens.search.reformulate import reformulate
from iralens.search.schema import SearchHit
from iralens.search.urls import canonical_url, domain_matches, registrable_host


def hit(title, url, engine="duckduckgo", pos=1, snippet="", published=None):
    return SearchHit(title=title, url=url, snippet=snippet, engine=engine, position=pos,
                     published_at=published, query="q")


# ---------------------------------------------------------- reformulation
def test_reformulate_keeps_original_first_and_strategies_tagged():
    out = reformulate("What is the price of a used car and how to fix it?", max_queries=10)
    assert out[0] == {"text": "What is the price of a used car and how to fix it?", "strategy": "original"}
    strategies = [q["strategy"] for q in out]
    assert "keywords" in strategies and "sub_question" in strategies and "synonym" in strategies
    synonym = next(q for q in out if q["strategy"] == "synonym")
    assert "automobile" in synonym["text"]


def test_reformulate_caps_and_dedups():
    assert len(reformulate("alpha beta gamma delta epsilon", max_queries=1)) == 1
    assert reformulate("   ") == []


# ------------------------------------------------------------ canonical URLs
def test_canonical_url_strips_tracking_fragments_and_www():
    a = canonical_url("http://WWW.Example.com/Page/?utm_source=x&b=2&a=1#frag")
    b = canonical_url("https://example.com/Page?a=1&b=2")
    assert a == b == "https://example.com/Page?a=1&b=2"


def test_canonical_url_keeps_path_case_and_rejects_non_web():
    assert canonical_url("https://e.com/A") != canonical_url("https://e.com/a")
    assert canonical_url("mailto:x@y.z") is None
    assert canonical_url("javascript:alert(1)") is None


def test_domain_matching_and_registrable_host():
    assert domain_matches("news.example.com", "example.com")
    assert not domain_matches("notexample.com", "example.com")
    assert registrable_host("https://a.b.example.com/x") == "example.com"


# ----------------------------------------------------------------- dedup
def test_dedup_merges_canonical_duplicates_and_logs_reason():
    hits = [hit("A page", "https://x.test/p?utm_source=a", pos=1),
            hit("A page", "https://x.test/p", engine="bing", pos=3)]
    groups, log = dedup(hits, threshold=0.85)
    assert len(groups) == 1 and len(groups[0].hits) == 2
    assert log[0]["reason"] == "canonical_url"


def test_dedup_merges_near_duplicate_titles_on_same_host_only():
    hits = [hit("Solar cell efficiency study 2025", "https://a.example.com/one"),
            hit("Solar cell efficiency study 2025!", "https://b.example.com/two"),
            hit("Solar cell efficiency study 2025", "https://other.org/three")]
    groups, log = dedup(hits, threshold=0.85)
    assert len(groups) == 2
    assert any(entry["reason"] == "near_duplicate" for entry in log)
    assert title_similarity("a b c", "a b d") == 0.5


def test_dedup_drops_non_web_urls():
    groups, _ = dedup([hit("x", "javascript:void(0)")], threshold=0.85)
    assert groups == []


# ---------------------------------------------------------------- ranking
def test_relevance_prefers_higher_rank_and_term_overlap():
    top = relevance(1, "solar cell efficiency", "", "solar cell efficiency")
    low = relevance(8, "unrelated", "", "solar cell efficiency")
    assert top > low


def test_authority_uses_longest_matching_suffix_and_default():
    weights = {".gov": 0.9, ".example.gov": 0.95, ".org": 0.6}
    assert authority("https://a.example.gov/x", weights, 0.5) == 0.95
    assert authority("https://foo.org/", weights, 0.5) == 0.6
    assert authority("https://foo.com/", weights, 0.5) == 0.5


def test_freshness_decays_and_unknown_is_neutral():
    today = date(2026, 1, 1)
    assert freshness("2026-01-01", today, 365, 0.5) == 1.0
    assert abs(freshness("2025-01-01", today, 365, 0.5) - 0.5) < 0.01
    assert freshness(None, today, 365, 0.5) == 0.5
    assert freshness("garbage", today, 365, 0.5) == 0.5


def test_combine_is_weighted_mean():
    assert combine({"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}) == 0.5


# ------------------------------------------- symbol-aware & phrase relevance
def test_cpp_query_does_not_match_bare_letter_pages():
    """Regression: with \\w+ tokenization, 'C++' collapsed to 'c', so pages
    that merely mention the letter C scored as fully relevant."""
    real = relevance(3, "Learn C++: free C++ tutorial", "vectors and more", "C++ tutorial")
    fake = relevance(3, "The letter C in programming", "c is a letter", "C++ tutorial")
    assert real > fake
    assert fake < 0.5


def test_csharp_and_dotnet_tokenized_consistently():
    assert relevance(1, "C# events tutorial", "", "C# events") > 0.9
    assert relevance(1, "Migrating to .NET 8", "", ".NET migration guide") > 0.5


def test_quoted_phrase_coverage_is_credited():
    with_phrase = relevance(2, 'Understanding "atomic operations" in practice', "",
                            'what are "atomic operations"')
    without = relevance(2, "Operations on atoms and molecules", "",
                        'what are "atomic operations"')
    assert with_phrase > without


def test_relevance_unchanged_for_queries_without_phrases():
    # classic formula preserved: 0.5 * 1/rank + 0.5 * overlap
    assert relevance(1, "solar cell efficiency", "", "solar cell efficiency") == 1.0
    assert relevance(4, "solar cell efficiency", "", "solar cell efficiency") == \
        round(0.5 * 0.25 + 0.5, 4)


# ----------------------------------------------------- versioned-page dedup
def test_dedup_keeps_distinctly_versioned_pages():
    hits = [hit("Python documentation", "https://docs.example.org/3.10/"),
            hit("Python documentation", "https://docs.example.org/3.12/")]
    groups, log = dedup(hits, threshold=0.85)
    assert len(groups) == 2                       # same title, different versions
    assert not any(e["reason"] == "near_duplicate" for e in log)


def test_dedup_keeps_v_style_versions():
    hits = [hit("Release notes", "https://a.example.org/v1/notes"),
            hit("Release notes", "https://a.example.org/v2/notes")]
    groups, _ = dedup(hits, threshold=0.85)
    assert len(groups) == 2


def test_dedup_still_merges_unversioned_near_duplicates():
    hits = [hit("Release notes today", "https://a.example.org/notes-one"),
            hit("Release notes today!", "https://a.example.org/notes-two")]
    groups, log = dedup(hits, threshold=0.85)
    assert len(groups) == 1
    assert log and log[-1]["reason"] == "near_duplicate"


def test_version_segments():
    from iralens.search.dedup import version_segments
    assert version_segments("https://d.org/3.10/library/") == ("3.10",)
    assert version_segments("https://d.org/v2/api") == ("v2",)
    assert version_segments("https://d.org/docs/intro") == ()
