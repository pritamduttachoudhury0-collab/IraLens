# -*- coding: utf-8 -*-
"""Live integration tests — the system tested as ONE product (spec Tests A–G).

These hit the real network and the real browser engine; they are skipped
automatically when either is unavailable.
"""

import json
import subprocess

import pytest

from halfiralens import HalfIraLens
from halfiralens.errors import (
    HalfIraLensError,
    PageUnavailableError,
    SessionStateError,
)

from .conftest import browser, live


@pytest.fixture
def hil(isolated_home):
    instance = HalfIraLens()
    yield instance
    instance.close()


# ---------------------------------------------------------- Test A — search
@browser
def test_a_web_search_open_extract(hil):
    results = hil.search("what is the capital of France", limit=5)
    assert 1 <= len(results) <= 5
    first = results[0]
    assert first.url.startswith("http")
    assert first.kind == "search_result"
    assert first.discovered_from.startswith("search:")

    page = hil.open(first.url, discovered_from=first.discovered_from)
    assert page.content.strip()
    assert page.discovered_from == first.discovered_from
    assert page.retrieval_method in ("static-reader", "browser")


# -------------------------------------------------- Test B — specialized src
@live
def test_b_scrape_bulk_mixed_results(hil):
    results = hil.scrape(
        ["https://example.com", "https://this-host-does-not-exist-zzz123.example"],
        mode="static",
    )
    assert len(results) == 2
    assert results[0].title  # first succeeded
    assert results[1]["error"] == "page_unavailable"  # failure isolated per URL


@live
def test_b_rss_structured_data(hil):
    feed = hil.fetch("rss", "read", url="https://hnrss.org/frontpage", limit=5)
    assert feed.source == "rss"
    assert feed.kind == "feed"
    assert feed.metadata["returned"] >= 1
    assert feed.metadata["entries"][0]["url"].startswith("http")
    assert feed.retrieval_method == "local:feedparser"


@live
def test_b_github_structured_data(hil):
    repos = hil.fetch("github", "search_repos", query="headless browser", limit=3)
    assert repos, "expected public repo results"
    top = repos[0]
    assert top.kind == "repo"
    assert top.metadata.get("stars") is not None
    assert top.retrieval_method in ("api:github", "cli:gh")


@live
def test_b_v2ex_structured_data(hil):
    topics = hil.fetch("v2ex", "hot")
    assert topics and topics[0].source == "v2ex"
    assert topics[0].retrieval_method == "api:v2ex"


# --------------------------------------- Test C — cross-capability navigation
@browser
def test_c_discover_then_browse(hil):
    """URL found via one capability is browsed with another, seamlessly."""
    repos = hil.fetch("github", "search_repos", query="example", limit=1)
    url = repos[0].url

    # specialized read
    via_source = hil.open(url, mode="source")
    assert via_source.source == "github"

    # same URL, full browser — same session object, no re-authentication of
    # any kind, no visible boundary
    hil.navigate(url)
    snapshot = hil.snapshot(max_chars=500)
    assert "github" in str(snapshot).lower() or repos[0].title.split("/")[0] in str(snapshot)

    state = hil.session_state()
    sources_seen = {h["source"] for h in state["history"]}
    assert {"github", "web"} <= sources_seen


# ------------------------------------------------ Test D — multi-step browse
@browser
def test_d_multi_step_browsing(hil):
    hil.navigate("https://example.com")
    snap1 = hil.snapshot(max_chars=300)
    assert "Example" in str(snap1)

    hil.navigate("https://www.iana.org/help/example-domains")
    hil.back()
    current = hil.engine().current_url_title()
    assert "example.com" in current["url"]

    links = hil.links(limit=10)
    assert isinstance(links, list)


# --------------------------------------------------------- Test E — session
@browser
def test_e_session_state_retained(hil):
    hil.navigate("https://example.com")
    hil.cookies_set("hil_test", "1", domain="example.com")
    cookies = hil.cookies_get()
    assert "hil_test" in json.dumps(cookies)
    hil.close()  # persists storage state

    # Fresh facade in the same home → browser state is restored.
    hil2 = HalfIraLens()
    try:
        state = hil2.session_state()
        assert state["browser_state_persisted"] is True
        assert any(h["url"].startswith("https://example.com") for h in state["history"])
        hil2.navigate("https://example.com")
        restored = json.dumps(hil2.cookies_get())
        assert "hil_test" in restored, "cookie should survive engine restart via session state"
    finally:
        hil2.close()


# ------------------------------------------------- Test F — failure recovery
@browser
def test_f_failure_recovery(hil):
    with pytest.raises(HalfIraLensError) as exc_info:
        hil.open("https://this-host-does-not-exist-zzz123.example")
    assert isinstance(exc_info.value, PageUnavailableError)
    payload = exc_info.value.to_dict()
    blob = json.dumps(payload).lower()
    for term in ("obscura", "agent-reach", "agent_reach"):
        assert term not in blob

    # system keeps working
    page = hil.open("https://example.com")
    assert page.title


@browser
def test_f_bad_tab_recovers(hil):
    hil.navigate("https://example.com")
    with pytest.raises(SessionStateError):
        hil.tab_switch("no-such-tab")
    assert "Example" in str(hil.snapshot(max_chars=200))


# ------------------------------------------- Test G — interface purity
@live
def test_g_mcp_surface_is_pure():
    from halfiralens.mcp_server import TOOLS, _SERVER_INFO

    assert _SERVER_INFO["name"] == "half-iralens"
    blob = json.dumps(TOOLS, ensure_ascii=False).lower()
    for term in ("obscura", "agent-reach", "agent_reach", "agentreach"):
        assert term not in blob, f"implementation ancestry leaked into MCP surface: {term}"
    names = [t["name"] for t in TOOLS]
    assert not any(n.startswith(("agent_reach", "obscura", "browser_ar")) for n in names)


@live
def test_g_cli_help_is_pure():
    out = subprocess.run(["halfiralens", "--help"], capture_output=True, text=True, timeout=30)
    blob = (out.stdout + out.stderr).lower()
    for term in ("obscura", "agent-reach", "agent_reach"):
        assert term not in blob


@live
def test_g_doctor_is_pure(hil):
    blob = json.dumps(hil.doctor(), ensure_ascii=False).lower()
    for term in ("obscura", "agent-reach", "agent_reach"):
        assert term not in blob


@browser
def test_g_artifact_structure_is_pure(hil):
    artifact = hil.open("https://example.com")
    structural = json.dumps(
        {k: v for k, v in artifact.to_dict().items() if k != "content"}, ensure_ascii=False
    ).lower()
    for term in ("obscura", "agent-reach", "agent_reach"):
        assert term not in structural


@live
def test_g_one_system_not_two(hil):
    """The AI-facing surface offers capabilities, not products."""
    catalog = {s["name"]: s for s in hil.sources()}
    # one web-reading capability, one search capability — not per-product tools
    assert "web" in catalog and "web-search" in catalog
    # browser operations are facade methods, not a separate product's tools
    for method in ("navigate", "click", "fill", "extract", "screenshot", "tab_list"):
        assert callable(getattr(hil, method))
    # and a single entry point for everything
    for method in ("search", "open", "read", "fetch", "doctor", "session_state"):
        assert callable(getattr(hil, method))
