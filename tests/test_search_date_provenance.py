# -*- coding: utf-8 -*-
"""F-08: the legacy search artifact must expose where its dates came from.

`SearchHit`/`RankedResult` already carry `date_evidence`, `date_kind` and
`modified_at`, but the legacy `to_artifacts()` adapter dropped all three. A
caller of the original `IraLens.search()` saw `published_at: 2015-03-13` and
`date_status: known` with no way to tell whether that date came from a
provider field, was parsed out of a snippet, or was guessed from a URL path --
which is why the suspect 2015 date on a Python 3.13 page could not be
explained from the artifact alone.

Nothing here asserts a *cause* for that anomaly: the raw engine response was
never captured, so it stays "unresolved". What is asserted is that every
non-null date reaching a legacy consumer is now traceable.
"""

from __future__ import annotations

from typing import Any

from iralens.search import to_artifacts
from iralens.search.schema import EngineOutcome, RankedResult, SearchResponse

URL = "https://docs.python.org/3.13/library/asyncio-task.html"


def _result(**overrides: Any) -> RankedResult:
    base = dict(
        title="Coroutines and Tasks",
        url=URL,
        canonical_url=URL,
        snippet="A coroutine is a function that can suspend its execution.",
        score=0.7,
        score_breakdown={"relevance": 0.7, "authority": 0.5, "freshness": 0.4,
                         "agreement": 0.5},
        engines=["duckduckgo"],
        engine_ranks={"duckduckgo": 1},
        published_at=None,
        date_status="unknown",
        queries=["python asyncio"],
        security_flags=[],
        merged_from=[],
        date_evidence="",
        modified_at=None,
        date_kind="",
    )
    base.update(overrides)
    return RankedResult(**base)


def _response(results) -> SearchResponse:
    results = list(results)
    return SearchResponse(
        query="python asyncio",
        results=results,
        queries=[{"text": "python asyncio", "strategy": "original"}],
        outcomes=[EngineOutcome(engine="duckduckgo", status="results", kind="",
                                count=len(results))],
        fallbacks=[],
        filters={},
        cache={},
        dedup_log=[],
        no_results_reason="none",
        summary=f"{len(results)} results",
    )


# --------------------------------------------------------------- provenance
def test_date_evidence_and_kind_reach_the_artifact():
    art = to_artifacts(_response([_result(
        published_at="2024-10-07T00:00:00Z",
        date_status="known",
        date_evidence="provider:published",
        date_kind="publication",
    )]), "python asyncio")[0]

    assert art.metadata["published_at"] == "2024-10-07T00:00:00Z"
    assert art.metadata["date_evidence"] == "provider:published"
    assert art.metadata["date_kind"] == "publication"
    assert art.provenance["date_evidence"] == "provider:published"
    assert art.provenance["date_kind"] == "publication"


def test_snippet_extracted_dates_are_distinguishable_from_provider_dates():
    """The whole point: a provider date and a guessed date must not look alike."""
    provider = to_artifacts(_response([_result(
        published_at="2024-10-07", date_status="known",
        date_evidence="provider:published", date_kind="publication")]), "q")[0]
    snippet = to_artifacts(_response([_result(
        published_at="2015-03-13", date_status="known",
        date_evidence="snippet:iso_date", date_kind="unknown")]), "q")[0]

    assert provider.provenance["date_evidence"] != snippet.provenance["date_evidence"]
    assert provider.provenance["date_kind"] == "publication"
    assert snippet.provenance["date_kind"] == "unknown"


def test_modified_at_is_carried_and_never_merged_into_published_at():
    art = to_artifacts(_response([_result(
        published_at="2024-10-07", date_status="known",
        date_evidence="provider:published", date_kind="publication",
        modified_at="2026-01-02")]), "q")[0]

    assert art.metadata["published_at"] == "2024-10-07"
    assert art.metadata["modified_at"] == "2026-01-02"
    assert art.provenance["modified_at"] == "2026-01-02"
    # A revision stamp is separate provenance, not a publication date.
    assert art.metadata["published_at"] != art.metadata["modified_at"]


def test_unknown_dates_remain_inspectable():
    """A suspicious or absent date is reported as such, not asserted as fact."""
    art = to_artifacts(_response([_result()]), "q")[0]
    assert art.metadata["published_at"] is None
    assert art.metadata["date_status"] == "unknown"
    assert art.provenance["date_status"] == "unknown"
    assert art.provenance["date_evidence"] == ""
    # Absent evidence means absent, not "publication".
    assert art.provenance["date_kind"] in ("", None)


def test_missing_fields_are_omitted_rather_than_fabricated():
    art = to_artifacts(_response([_result()]), "q")[0]
    assert "date_evidence" not in art.metadata
    assert "modified_at" not in art.metadata
    # date_evidence / date_kind are always present in provenance (empty string)
    # so a consumer can distinguish "absent" from "not carried".
    assert art.provenance["date_evidence"] == ""
    assert art.provenance["date_kind"] == ""


def test_legacy_shape_is_preserved_for_existing_consumers():
    """New fields are additive: the artifact schema and keys are unchanged."""
    art = to_artifacts(_response([_result(
        published_at="2024-10-07", date_status="known",
        date_evidence="provider:published", date_kind="publication")]), "python asyncio")[0]

    assert art.source == "web-search"
    assert art.kind == "search_result"
    assert art.title == "Coroutines and Tasks"
    assert art.url == URL
    assert art.content == "A coroutine is a function that can suspend its execution."
    assert art.metadata["query"] == "python asyncio"
    assert art.metadata["rank"] == 1
    assert art.metadata["published_at"] == "2024-10-07"
    assert art.metadata["snippet"] == art.content
    assert art.retrieval_method == "search:duckduckgo"
    assert art.discovered_from == "search:python asyncio"
    for key in ("engines", "engine_ranks", "canonical_url", "score_breakdown",
                "queries", "merged_from", "security_flags", "date_status"):
        assert key in art.provenance


def test_every_non_null_date_carries_evidence():
    """Acceptance: no date reaches a legacy consumer without its provenance."""
    results = [
        _result(published_at="2024-10-07", date_status="known",
                date_evidence="provider:published", date_kind="publication"),
        _result(published_at="2015-03-13", date_status="known",
                date_evidence="url:path_date", date_kind="unknown",
                url="https://example.org/2015/03/13/post"),
        _result(),
    ]
    for art in to_artifacts(_response(results), "q"):
        published = art.metadata.get("published_at")
        if published:
            assert art.provenance.get("date_evidence"), (
                f"date {published} has no recorded evidence")
            assert art.provenance.get("date_kind") is not None
        else:
            assert art.metadata["date_status"] == "unknown"


# ---------------------------------------------- the reported anomaly, honestly
def test_the_reported_2015_anomaly_is_inspectable_not_asserted():
    """The 2015 date's *cause* is unresolved; the artifact must say so.

    The audit could not capture the raw engine response, so no cause is
    assigned here. What is guaranteed is that a legacy consumer can now see
    evidence and kind, and that an unexplained date is typed `unknown`
    rather than silently promoted to a verified publication date.
    """
    art = to_artifacts(_response([_result(
        published_at="2015-03-13", date_status="known",
        date_evidence="url:path_date", date_kind="unknown")]), "q")[0]

    assert art.metadata["published_at"] == "2015-03-13"
    # Typed as unknown, so freshness ranking does not treat it as publication.
    assert art.provenance["date_kind"] == "unknown"
    assert art.provenance["date_evidence"] == "url:path_date"
