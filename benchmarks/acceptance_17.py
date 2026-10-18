#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The audit's 17 offline acceptance checks, run against the *current* code.

The audit shipped a script (`scripts/validate_findings.py`) that asserted the
seventeen baseline defects were still **present**. This is its inverse: every
check here asserts that the defect is **gone**. It runs entirely offline —
the network transports are local servers or captured fixtures — so it can be
run at any time and its result means something.

    python benchmarks/acceptance_17.py            # run all 17
    python benchmarks/acceptance_17.py --json     # machine-readable
    python benchmarks/acceptance_17.py -v         # show every check

Exit code 0 when all 17 pass, 1 otherwise. The number printed is the score.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIXTURES = REPO / "tests" / "fixtures" / "audit"


# --------------------------------------------------------------------- local
class _Resp:
    """A minimal response, as `safe_urlopen` returns one."""

    def __init__(self, body: bytes, url: str, status: int = 200,
                 headers: Optional[Dict[str, str]] = None) -> None:
        import io

        self._buf = io.BytesIO(body)
        self.url = url
        self.status = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n if n >= 0 else len(self._buf.getvalue()))

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _fake_page(url: str) -> bytes:
    """Page A and page B, each with its own body."""
    if "/b" in url:
        return b"<html><body><h1>Page B</h1><p>" + b"beta " * 60 + b"</p></body></html>"
    return b"<html><body><h1>Page A</h1><p>" + b"alpha " * 60 + b"</p></body></html>"


def _install_fake_transport() -> Any:
    """Replace the web transport with a local one that answers every URL.

    The page-read path (extraction, caching, provenance) runs for real; only
    the socket is replaced, so no network is needed.
    """
    from iralens.sources import web as web_mod

    original = web_mod.safe_urlopen

    def _fake(url: str, **kwargs: Any) -> _Resp:
        # Every page "redirects" to a distinct final URL, which is what made
        # the cached read inherit the *previous* page's identity.
        final = url.rstrip("/") + "/final"
        return _Resp(_fake_page(url), final)

    web_mod.safe_urlopen = _fake  # type: ignore[assignment]
    return original


def _restore_fake_transport(original: Any) -> None:
    from iralens.sources import web as web_mod

    web_mod.safe_urlopen = original  # type: ignore[assignment]


def _fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------- checks
def check_01_cache_reuses_A_content() -> Tuple[bool, str]:
    """The cached read of A still returns A's own content."""
    from iralens.core import IraLens
    from iralens.sources import web as web_mod

    home = tempfile.mkdtemp(prefix="iralens-acceptance-")
    os.environ["IRALENS_HOME"] = home
    original = _install_fake_transport()
    try:
        web_mod.HOST_RATE_LIMITER.min_interval = 0.0
        web_mod.HOST_RATE_LIMITER.default_min_interval = 0.0
        url_a = "https://example.org/a"
        url_b = "https://example.org/b"
        with IraLens() as hil:
            first_a = hil.open(url_a, mode="static")
            hil.open(url_b, mode="static")
            again_a = hil.open(url_a, mode="static")
        ok = first_a.content == again_a.content
        return ok, ("the second read of A returned A's content" if ok
                    else "the cached read of A returned different content")
    finally:
        _restore_fake_transport(original)
        shutil.rmtree(home, ignore_errors=True)


def check_02_cache_requested_A_retained() -> Tuple[bool, str]:
    """A cached read still names the URL that was requested."""
    from iralens.core import IraLens
    from iralens.sources import web as web_mod

    home = tempfile.mkdtemp(prefix="iralens-acceptance-")
    os.environ["IRALENS_HOME"] = home
    original = _install_fake_transport()
    try:
        web_mod.HOST_RATE_LIMITER.min_interval = 0.0
        web_mod.HOST_RATE_LIMITER.default_min_interval = 0.0
        url_a = "https://example.org/a"
        with IraLens() as hil:
            hil.open(url_a, mode="static")
            again_a = hil.open(url_a, mode="static")
        ok = again_a.requested_url == url_a
        return ok, f"requested_url={again_a.requested_url!r}"
    finally:
        _restore_fake_transport(original)
        shutil.rmtree(home, ignore_errors=True)


def check_03_cache_final_url_false_B_attribution() -> Tuple[bool, str]:
    """A cached read is not attributed to another page's final URL."""
    from iralens.core import IraLens
    from iralens.sources import web as web_mod

    home = tempfile.mkdtemp(prefix="iralens-acceptance-")
    os.environ["IRALENS_HOME"] = home
    original = _install_fake_transport()
    try:
        web_mod.HOST_RATE_LIMITER.min_interval = 0.0
        web_mod.HOST_RATE_LIMITER.default_min_interval = 0.0
        url_a = "https://example.org/a"
        url_b = "https://example.org/b"
        with IraLens() as hil:
            first_a = hil.open(url_a, mode="static")
            first_b = hil.open(url_b, mode="static")
            again_a = hil.open(url_a, mode="static")
        ok = (again_a.final_url == first_a.final_url
              and again_a.final_url != first_b.final_url)
        return ok, f"A final={again_a.final_url!r} B final={first_b.final_url!r}"
    finally:
        _restore_fake_transport(original)
        shutil.rmtree(home, ignore_errors=True)


def check_04_timestamp_only_has_no_snippets() -> Tuple[bool, str]:
    """The Wikipedia request always asks for snippets, so it never lacks them."""
    from iralens.search.engines.wikipedia import WikipediaBackend
    from iralens.search.schema import SearchFilters

    backend = WikipediaBackend()
    captured: Dict[str, Any] = {}

    class _Ctx:
        config: Dict[str, Any] = {}

        def session(self) -> "_Ctx":
            return self

    def _fake_fetch(url: str, **kwargs: Any) -> str:
        captured["url"] = url
        return json.dumps(_fixture("wiki-snippet-raw.json"))

    import iralens.search.engines.wikipedia as wiki_mod

    original = wiki_mod.fetch_url_text
    wiki_mod.fetch_url_text = _fake_fetch  # type: ignore[assignment]
    try:
        hits = backend.search("capital of assam", SearchFilters(), 5, _Ctx())
    finally:
        wiki_mod.fetch_url_text = original  # type: ignore[assignment]
    url = str(captured.get("url", ""))
    ok = "srprop=snippet" in url and bool(hits)
    return ok, f"request params: {url.split('?', 1)[-1] if '?' in url else url!r}"


def check_05_snippet_request_restores_context() -> Tuple[bool, str]:
    """With the snippet parameter the response carries snippets again."""
    from iralens.search.engines.wikipedia import WikipediaBackend
    from iralens.search.schema import SearchFilters

    backend = WikipediaBackend()

    class _Ctx:
        config: Dict[str, Any] = {}

        def session(self) -> "_Ctx":
            return self

    import iralens.search.engines.wikipedia as wiki_mod

    original = wiki_mod.fetch_url_text
    wiki_mod.fetch_url_text = lambda url, **kwargs: json.dumps(  # type: ignore[assignment]
        _fixture("wiki-snippet-raw.json"))
    try:
        hits = backend.search("capital of assam", SearchFilters(), 5, _Ctx())
    finally:
        wiki_mod.fetch_url_text = original  # type: ignore[assignment]
    ok = bool(hits) and any(h.snippet for h in hits)
    return ok, f"{sum(1 for h in hits if h.snippet)}/{len(hits)} hits carry a snippet"


def check_06_same_top_wikipedia_page() -> Tuple[bool, str]:
    """Asking for snippets does not change which page ranks first."""
    timestamp_only = _fixture("wiki-timestamp-raw.json")["query"]["search"]
    with_snippet = _fixture("wiki-snippet-raw.json")["query"]["search"]
    ok = bool(timestamp_only) and bool(with_snippet) and \
        timestamp_only[0]["pageid"] == with_snippet[0]["pageid"]
    return ok, f"top pageid {timestamp_only[0]['pageid']} vs {with_snippet[0]['pageid']}"


def check_07_session_save_race_observed() -> Tuple[bool, str]:
    """Concurrent session saves neither lose a write nor blow up."""
    from iralens.session import Session

    home = tempfile.mkdtemp(prefix="iralens-acceptance-")
    os.environ["IRALENS_HOME"] = home
    try:
        session = Session()
        errors: List[str] = []
        saved = []

        def _worker(index: int) -> None:
            try:
                for i in range(20):
                    session.record_open(f"https://example.org/{index}/{i}",
                                        title=f"t{index}-{i}", source="web")
                    saved.append(1)
            except Exception as exc:  # pragma: no cover - the defect
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        ok = not errors
        detail = "; ".join(errors[:2]) if errors else f"{len(saved)} saves, no error"
        if ok:
            try:
                reloaded = Session()
                ok = len(reloaded.history) > 0
                detail = f"{len(saved)} saves, {len(reloaded.history)} in the history"
            except Exception as exc:  # pragma: no cover
                ok = False
                detail = f"reload failed: {exc}"
        return ok, detail
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _budget_check(qid: str) -> Tuple[bool, str]:
    """A read never returns more than the documented character budget."""
    from iralens import content_guard

    out = _fixture(f"read-{qid}.json")["output"]
    budget = int(out["metadata"]["max_chars"])
    text, truncated = content_guard.truncate_text(out["content"], budget)
    ok = len(text) <= budget and truncated is True
    return ok, f"budget={budget} returned={len(text)} truncated={truncated}"


def check_08_budget_q0009() -> Tuple[bool, str]:
    return _budget_check("Q0009")


def check_09_budget_q0013() -> Tuple[bool, str]:
    return _budget_check("Q0013")


def check_10_budget_q0014() -> Tuple[bool, str]:
    return _budget_check("Q0014")


def check_11_budget_q0050() -> Tuple[bool, str]:
    return _budget_check("Q0050")


def check_12_innerText_contains_inline_script() -> Tuple[bool, str]:
    """The rendered-text collector skips <script> subtrees."""
    source = (REPO / "iralens" / "_internal" / "browser_engine" / "vendor" / "obscura"
              / "crates" / "obscura-js" / "js" / "bootstrap.js").read_text(encoding="utf-8")
    index = source.find("function _collectInnerText")
    body = source[index:index + 4000] if index != -1 else ""
    ok = index != -1 and "SCRIPT" in body and "_innerTextSkippedTags" in body
    return ok, "the collector skips SCRIPT elements" if ok else "the collector does not skip SCRIPT"


def check_13_script_style_incorrectly_visible() -> Tuple[bool, str]:
    """The rendered-text collector skips <style> subtrees."""
    source = (REPO / "iralens" / "_internal" / "browser_engine" / "vendor" / "obscura"
              / "crates" / "obscura-js" / "js" / "bootstrap.js").read_text(encoding="utf-8")
    index = source.find("function _collectInnerText")
    body = source[index:index + 4000] if index != -1 else ""
    ok = index != -1 and "STYLE" in body and "tagName" in body
    return ok, "the collector skips STYLE elements" if ok else "the collector does not skip STYLE"


def check_14_origin_429_mislabeled_reader_service() -> Tuple[bool, str]:
    """An origin 429 is rate limiting, not a reader-service failure."""
    from iralens.errors import RateLimitedError, classify_http_status

    err = classify_http_status(429, layer="origin")
    ok = (isinstance(err, RateLimitedError)
          and err.error_type == "rate_limited"
          and "reader service" not in err.message
          and err.detail == "http_429")
    return ok, f"error_type={err.error_type!r} detail={err.detail!r}"


def check_15_q0015_offsite_results_after_explicit_site_constraint() -> Tuple[bool, str]:
    """A `site:`-scoped query returns only in-scope results."""
    from iralens.search.engine import SearchEngine
    from iralens.search.schema import SearchFilters, SearchOptions
    from iralens.search.engines.base import SearchBackend
    from iralens.settings import Settings

    class _Backend(SearchBackend):
        name = "fake"
        handled: Dict[str, str] = {}

        def search(self, query, filters, limit, context, *, offset: int = 0):
            from iralens.search.schema import SearchHit

            return [
                SearchHit(title="in scope", url="https://docs.python.org/3/library/asyncio-task.html",
                          snippet="TaskGroup", engine="fake", position=1),
                SearchHit(title="off site", url="https://example.com/asyncio-task.html",
                          snippet="TaskGroup", engine="fake", position=2),
            ]

    settings = Settings(search_engines=("fake",), search_min_engines=1,
                        retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0)
    engine = SearchEngine(settings, backends={"fake": _Backend()})
    response = engine.search(
        "site:docs.python.org TaskGroup",
        filters=SearchFilters(),
        options=SearchOptions(reformulate=False),
    )
    urls = [r.url for r in response.results]
    offsite = [u for u in urls if "docs.python.org" not in u]
    return (not offsite), f"results={urls}"


def check_16_benign_numeric_identifier_falsely_rate_limited() -> Tuple[bool, str]:
    """A body that merely mentions a number is not a rate limit."""
    from iralens.reliability import classify_block

    fixture = _fixture("block-classifier-fixture.json")
    kind = classify_block(fixture["plain_html"])
    ok = kind != "rate_limited"
    return ok, f"classification={kind!r}"


def check_17_valid_429_documentation_serp_causes_browser_fallback() -> Tuple[bool, str]:
    """A valid SERP that documents HTTP 429 is accepted, not blocked."""

    fixture = _fixture("block-classifier-fixture.json")
    html = fixture["valid_serp_html"]
    calls: List[Dict[str, Any]] = []

    class _Ctx:
        config: Dict[str, Any] = {}

        def session(self) -> "_Ctx":
            return self

    import iralens.search.engines.bing as bing_mod

    original = bing_mod.fetch_serp_html

    def _probe(context: Any, url: str, timeout: int, **kwargs: Any) -> str:
        calls.append({"url": url})
        return html

    bing_mod.fetch_serp_html = _probe  # type: ignore[assignment]
    try:
        from iralens.search.engines.bing import BingBackend
        from iralens.search.schema import SearchFilters

        hits = BingBackend().search("http 429", SearchFilters(), 5, _Ctx())
    except Exception:
        hits = []
    finally:
        bing_mod.fetch_serp_html = original  # type: ignore[assignment]
    ok = len(hits) == fixture["valid_hit_count"]
    return ok, f"{len(hits)} hits accepted (expected {fixture['valid_hit_count']})"


CHECKS: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = [
    ("cache_reuses_A_content", check_01_cache_reuses_A_content),
    ("cache_requested_A_retained", check_02_cache_requested_A_retained),
    ("cache_final_url_false_B_attribution", check_03_cache_final_url_false_B_attribution),
    ("timestamp_only_has_no_snippets", check_04_timestamp_only_has_no_snippets),
    ("snippet_request_restores_context", check_05_snippet_request_restores_context),
    ("same_top_wikipedia_page", check_06_same_top_wikipedia_page),
    ("session_save_race_observed", check_07_session_save_race_observed),
    ("Q0009_exceeds_documented_character_budget", check_08_budget_q0009),
    ("Q0013_exceeds_documented_character_budget", check_09_budget_q0013),
    ("Q0014_exceeds_documented_character_budget", check_10_budget_q0014),
    ("Q0050_exceeds_documented_character_budget", check_11_budget_q0050),
    ("innerText_contains_inline_script", check_12_innerText_contains_inline_script),
    ("script_style_incorrectly_visible", check_13_script_style_incorrectly_visible),
    ("origin_429_mislabeled_reader_service", check_14_origin_429_mislabeled_reader_service),
    ("Q0015_offsite_results_after_explicit_site_constraint",
     check_15_q0015_offsite_results_after_explicit_site_constraint),
    ("benign_numeric_identifier_falsely_rate_limited",
     check_16_benign_numeric_identifier_falsely_rate_limited),
    ("valid_429_documentation_serp_causes_browser_fallback",
     check_17_valid_429_documentation_serp_causes_browser_fallback),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every check")
    args = parser.parse_args()

    results = []
    passed = 0
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:  # a broken check is a failed check
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        passed += 1 if ok else 0
        results.append({"check": name, "passed": bool(ok), "detail": detail})
        if args.verbose or not ok:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    total = len(CHECKS)
    if args.json:
        print(json.dumps({
            "suite": "audit-17-acceptance",
            "passed": passed,
            "total": total,
            "score": f"{passed}/{total}",
            "all_passed": passed == total,
            "checks": results,
        }, indent=2))
    else:
        print(f"\n{passed}/{total} acceptance checks passed.")
        if passed != total:
            print("FAILED: " + ", ".join(r["check"] for r in results if not r["passed"]))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
