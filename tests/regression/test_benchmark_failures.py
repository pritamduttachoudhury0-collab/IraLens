# -*- coding: utf-8 -*-
"""Reproductions of benchmark defects D1-D8 (improvement prompt §3).

Phase 0: each test encodes the *desired* behaviour for a recorded failure
shape (fixtures modelled on benchmark report §2/§53-54 examples, e.g.
R5-Q283/Q297/Q267) and is marked xfail until its phase fixes it. A phase
removes the marker in the same commit as its fix; markers must never be
removed without the behaviour changing.
"""
import pytest

from iralens.search.ranking import relevance
from iralens.search.schema import SearchHit


def hit(title, url, engine="bing", pos=1, snippet="", published=None):
    return SearchHit(title=title, url=url, snippet=snippet, engine=engine,
                     position=pos, published_at=published, query="q")


def test_d1_specific_official_page_beats_hub():
    # Realistic sparse-snippet shape from the benchmark: the hub arrives at
    # rank 1 with a generic snippet; the specific official page at rank 2.
    q = "register a partnership firm Assam"
    hub = relevance(1, "Professional Tax", "", q, url="https://en.wikipedia.org/wiki/Professional_Tax")
    specific = relevance(2, "Registration of Firms",
                         "Register a partnership firm in Assam: fee Rs 5, maximum 20 partners", q,
                         url="https://finance.assam.gov.in/registration-of-firms")
    assert specific > hub


def test_d1_dictionary_page_demoted_for_factual_query():
    q = "how many members in Lok Sabha"
    dictionary = relevance(1, "many", "", q, url="https://www.merriam-webster.com/dictionary/many")
    answer = relevance(3, "Lok Sabha - House of the People", "The Lok Sabha has 543 elected members", q,
                       url="https://loksabha.nic.in/members")
    assert answer > dictionary


def test_d2_procedure_query_prefers_jurisdiction_official_domain():
    from iralens.search.intent import classify_query, intent_multiplier
    info = classify_query("register a partnership firm Assam")
    assert info.answer_type == "procedure"
    assert info.jurisdiction_domains  # expects e.g. .gov.in / assam.gov.in preference
    assert intent_multiplier("https://finance.assam.gov.in/firms", info) > \
        intent_multiplier("https://en.wikipedia.org/wiki/Partnership", info)


def test_d2_error_and_comparison_classification_generalises():
    from iralens.search.intent import classify_query
    assert classify_query("Kafka consumer ERR_UNKNOWN_MEMBER_ID rebalance fix").answer_type == "error"
    assert classify_query("PPF vs ELSS returns comparison").answer_type == "comparison"
    assert classify_query("India GDP growth rate 2026").answer_type == "statistic"


@pytest.mark.xfail(reason="D3: non-Latin tokenisation/language routing", strict=False)
def test_d3_yoruba_query_terms_not_empty_and_wikipedia_language():
    from iralens.search.querytext import term_set
    from iralens.search.language import detect_language
    q = "Kí ni orúkọ ìlú ńlá tí ó tóbi jù ní Nàìjíríà"
    assert len(term_set(q)) >= 3
    assert detect_language(q).wikipedia_lang in {"yo", "en"}


@pytest.mark.xfail(reason="D4: dead first engine invisible to caller", strict=False)
def test_d4_response_exposes_degradation_detail():
    from iralens.search.schema import SearchResponse
    assert hasattr(SearchResponse, "degradation_detail")


@pytest.mark.xfail(reason="D5: Wikipedia hub fills procedure slot", strict=False)
def test_d5_wikipedia_hub_demoted_for_procedure():
    q = "solar rooftop net metering Assam"
    wiki_hub = relevance(1, "Solar power in India", "Solar power in India overview", q)
    official = relevance(2, "Net Metering - Assam Energy Development Agency",
                         "Apply for solar rooftop net metering in Assam, AERC order, forms and fees", q)
    assert official > wiki_hub


@pytest.mark.xfail(reason="D6: short result list returned silently", strict=False)
def test_d6_shortfall_reason_present():
    from iralens.search.schema import SearchResponse
    assert "shortfall_reason" in SearchResponse.__dataclass_fields__


@pytest.mark.xfail(reason="D7: published_at missing when snippet carries a date", strict=False)
def test_d7_date_extracted_from_snippet():
    from iralens.search.freshness_extract import extract_date
    found, source = extract_date("Updated on 5 August 2026 by the commission", "https://example.gov.in/order")
    assert found == "2026-08-05" and source


@pytest.mark.xfail(reason="D8: wrapper/test-host URLs ranked and cited as-is", strict=False)
def test_d8_wrapper_unwrapped_and_test_host_flagged():
    from iralens.search.urls import unwrap_wrapper, is_test_host
    assert unwrap_wrapper("https://translate.google.com/translate?u=https%3A%2F%2Fexample.com%2Fa") == "https://example.com/a"
    assert is_test_host("https://uat.startupindia.gov.in/x")
