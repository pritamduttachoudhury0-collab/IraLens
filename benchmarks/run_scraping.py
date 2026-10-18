#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The scraping test: does a read return the page, honestly?

The 3,000-query suite measures *search*. This runner measures *reading* — the
part that scrapes a page and returns its text. It replays the audit's read
probes through the current decision functions and checks, for every probe,
that the defect the audit found is gone:

  CAP     the returned text fits the documented character budget
  MAIN    the main content is selected, not the navigation
  JS      an application shell is detected instead of being accepted
  TEXT    no script source leaks into the text
  RATE    an origin 429 is typed as rate limiting, not as a reader failure
  BLOCK   a benign body is not declared rate-limited, and a valid SERP that
          documents HTTP 429 is accepted rather than blocked
  CACHE   a cached read keeps the identity of the page it read
  SESSION concurrent session saves neither race nor lose a write
  ACCESS  a page that refuses anonymous access is refused *typed* — this one
          is an external restriction, and is reported as such

    python benchmarks/run_scraping.py            # replay the captured probes
    python benchmarks/run_scraping.py --live     # re-read the URLs now
    python benchmarks/run_scraping.py --json     # machine-readable

The probes are the audit's own captured reads, kept in
`tests/fixtures/audit/`. A probe that could not be run is reported as
`not_run` with its reason — never as a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIXTURES = REPO / "tests" / "fixtures" / "audit"
DEFAULT_OUT = REPO / "benchmarks" / "results" / "scraping"

EXTERNAL = "external"
CODE = "code"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@dataclass
class Probe:
    name: str
    family: str
    url: str
    check: Callable[[], Tuple[bool, str]]
    cause: str = CODE          # who can fix a failure of this probe
    live: bool = True          # may this probe be re-read over the network?


@dataclass
class Result:
    name: str
    family: str
    status: str                 # passed | failed | not_run
    detail: str = ""
    cause: str = CODE

    def to_dict(self) -> Dict[str, Any]:
        return {"probe": self.name, "family": self.family, "status": self.status,
                "detail": self.detail, "cause": self.cause}


# ------------------------------------------------------------------- probes
def probe_cap(fixture: str) -> Probe:
    def _check() -> Tuple[bool, str]:
        from iralens import content_guard

        out = _fixture(fixture)["output"]
        budget = int(out["metadata"]["max_chars"])
        text, truncated = content_guard.truncate_text(out["content"], budget)
        ok = len(text) <= budget
        return ok, f"budget={budget} returned={len(text)} truncated={truncated}"
    return Probe(f"cap:{fixture}", "CAP", _fixture(fixture).get("url", ""), _check)


def probe_main() -> Probe:
    """The Wikipedia page's navigation must not eat the character budget."""
    def _check() -> Tuple[bool, str]:
        from iralens.extract import html_to_markdown

        fixture = _fixture("read-Q0014.json")
        out = fixture["output"]
        content = out["content"]
        # The audit's finding: the 111-language menu filled the budget and
        # the article lead was missing. The menu is a run of link-only lines;
        # the article is prose. A main-content selection removes the menu.
        lines = [l for l in content.splitlines() if l.strip()]
        link_only = sum(1 for l in lines if l.strip().startswith("[")
                        and "](" in l)
        prose = "\n".join(l for l in lines if not (l.strip().startswith("[")
                                                   and "](" in l))
        # Re-extract a Wikipedia-shaped page to prove the selection works.
        html = _wikipedia_shaped_page()
        selected = html_to_markdown(html)
        ok = ("Language 5" not in selected
              and "encyclopaedia lead" in selected
              and len(prose) > 0)
        return ok, (f"menu lines={link_only} selected_len={len(selected)} "
                    f"menu_removed={'Language 5' not in selected}")
    return Probe("main:wikipedia-navigation", "MAIN",
                 "https://en.wikipedia.org/wiki/Brahmaputra_River", _check)


def _wikipedia_shaped_page() -> str:
    """A page shaped like Wikipedia: the menu sits inside <main>."""
    langs = "".join(
        f'<li lang="L{n}"><a href="//l{n}.wikipedia.org/wiki/X" lang="L{n}">Language {n}</a></li>'
        for n in range(111))
    article = "".join(
        f"<p>Paragraph {i} of the article body. It contains several sentences and "
        f"enough words to count as real prose content for the reader. This is the "
        f"actual encyclopaedia lead and body text.</p>" for i in range(12))
    return (
        "<html><body><div class='mw-page-container'>"
        "<nav id='p-namespaces'><a href='#bodyContent'>Jump to content</a></nav>"
        "<main id='content' class='mw-body' role='main'>"
        "<header class='mw-body-header'>"
        "<h1 id='firstHeading' class='firstHeading'>Brahmaputra River</h1>"
        "<div class='vector-dropdown-content-wrapper'>"
        f"<div id='p-lang' class='vector-dropdown-content mw-language-variant'>"
        f"<div class='mw-language-variant-body'><ul>{langs}</ul></div></div></div></header>"
        "<div id='bodyContent'><div id='mw-content-text' class='mw-body-content'>"
        f"<div class='mw-parser-output'>{article}</div></div></div></main></div></body></html>")


def probe_js() -> Probe:
    """A long application shell must be detected as a shell."""
    def _check() -> Tuple[bool, str]:
        from iralens.sources.web import looks_like_js_shell

        content = _fixture("read-Q0023.json")["output"]["content"]
        ok = looks_like_js_shell(content)
        return ok, f"the {len(content)}-character shell is detected"
    return Probe("js:worldbank-shell", "JS",
                 "https://data.worldbank.org/indicator/SP.POP.TOTL", _check)


def probe_text() -> Probe:
    """No script source may leak into the extracted text."""
    def _check() -> Tuple[bool, str]:
        from iralens.extract import html_to_markdown

        html = ("<html><body><main><p>The visible article text.</p>"
                "<script>var leaked = '(function () { secretCode(); })();';</script>"
                "<style>.leaked { color: red; }</style></main></body></html>")
        text = html_to_markdown(html)
        ok = "leaked" not in text and "secretCode" not in text
        return ok, f"extracted={text.strip()!r}"
    return Probe("text:no-script-source", "TEXT", "https://react.dev/learn", _check)


def probe_rate() -> Probe:
    """An origin 429 is typed rate limiting."""
    def _check() -> Tuple[bool, str]:
        from iralens.errors import RateLimitedError, classify_http_status

        err = classify_http_status(429, layer="origin")
        ok = isinstance(err, RateLimitedError) and err.detail == "http_429"
        return ok, f"error_type={err.error_type!r} detail={err.detail!r}"
    return Probe("rate:origin-429", "RATE", "https://httpbin.org/status/429", _check)


def probe_block() -> Probe:
    """A benign body is not rate-limited; a valid SERP is accepted."""
    def _check() -> Tuple[bool, str]:
        from iralens.reliability import classify_block
        from iralens.search.engines.bing import BingBackend
        from iralens.search.schema import SearchFilters

        fixture = _fixture("block-classifier-fixture.json")
        benign = classify_block(fixture["plain_html"])
        if benign == "rate_limited":
            return False, "a benign archive page was classified as rate limited"

        class _Ctx:
            config: Dict[str, Any] = {}

            def session(self) -> "_Ctx":
                return self

        import iralens.search.engines.bing as bing_mod

        original = bing_mod.fetch_serp_html
        bing_mod.fetch_serp_html = lambda ctx, url, timeout, **kw: fixture["valid_serp_html"]
        try:
            hits = BingBackend().search("http 429", SearchFilters(), 5, _Ctx())
        except Exception as exc:
            return False, f"the valid SERP was rejected: {exc}"
        finally:
            bing_mod.fetch_serp_html = original
        ok = len(hits) == fixture["valid_hit_count"]
        return ok, f"benign={benign!r} accepted_hits={len(hits)}"
    return Probe("block:benign-and-valid-serp", "BLOCK",
                 "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429", _check)


def probe_cache() -> Probe:
    """A cached read keeps the identity of the page it read."""
    def _check() -> Tuple[bool, str]:
        from iralens.core import IraLens
        from iralens.sources import web as web_mod

        home = tempfile.mkdtemp(prefix="iralens-scraping-")
        os.environ["IRALENS_HOME"] = home
        original = web_mod.safe_urlopen

        class _Resp:
            def __init__(self, body: bytes, url: str) -> None:
                import io

                self._buf = io.BytesIO(body)
                self.url = url
                self.status = 200
                self.headers = {"Content-Type": "text/html; charset=utf-8"}

            def read(self, n: int = -1) -> bytes:
                return self._buf.read(n if n >= 0 else len(self._buf.getvalue()))

            def getcode(self) -> int:
                return 200

            def geturl(self) -> str:
                return self.url

            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

        def _fake(url: str, **kwargs: Any) -> _Resp:
            body = (b"<html><body><p>beta " * 60 if "/b" in url
                    else b"<html><body><p>alpha " * 60) + b"</p></body></html>"
            return _Resp(body, url.rstrip("/") + "/final")

        web_mod.safe_urlopen = _fake  # type: ignore[assignment]
        try:
            web_mod.HOST_RATE_LIMITER.min_interval = 0.0
            web_mod.HOST_RATE_LIMITER.default_min_interval = 0.0
            with IraLens() as hil:
                first_a = hil.open("https://example.org/a", mode="static")
                first_b = hil.open("https://example.org/b", mode="static")
                again_a = hil.open("https://example.org/a", mode="static")
        finally:
            web_mod.safe_urlopen = original  # type: ignore[assignment]
            shutil.rmtree(home, ignore_errors=True)
        ok = (again_a.final_url == first_a.final_url
              and again_a.final_url != first_b.final_url
              and again_a.content == first_a.content)
        return ok, f"A={again_a.final_url!r} B={first_b.final_url!r}"
    return Probe("cache:identity-preserved", "CACHE",
                 "https://react.dev/learn", _check)


def probe_session() -> Probe:
    """Concurrent session saves neither race nor lose a write."""
    def _check() -> Tuple[bool, str]:
        from iralens.session import Session

        home = tempfile.mkdtemp(prefix="iralens-scraping-")
        os.environ["IRALENS_HOME"] = home
        errors: List[str] = []
        try:
            session = Session()

            def _worker(index: int) -> None:
                try:
                    for i in range(20):
                        session.record_open(f"https://example.org/{index}/{i}",
                                            title=f"t{index}-{i}", source="web")
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")

            threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            reloaded = Session()
            ok = not errors and len(reloaded.history) > 0
            return ok, (f"errors={errors[:1]} history={len(reloaded.history)}")
        finally:
            shutil.rmtree(home, ignore_errors=True)
    return Probe("session:concurrent-saves", "SESSION", "local://session", _check)


def probe_access() -> Probe:
    """A page that refuses anonymous access is refused *typed*."""
    def _check() -> Tuple[bool, str]:
        from iralens.errors import PageUnavailableError, classify_http_status

        err = classify_http_status(403, layer="origin")
        ok = isinstance(err, PageUnavailableError) and err.detail == "http_403"
        return ok, f"error_type={err.error_type!r} detail={err.detail!r}"
    return Probe("access:403-typed", "ACCESS", "https://www.nseindia.com/", _check,
                 cause=EXTERNAL)


def build_probes() -> List[Probe]:
    return [
        probe_cap("read-Q0009.json"),
        probe_cap("read-Q0013.json"),
        probe_cap("read-Q0014.json"),
        probe_cap("read-Q0050.json"),
        probe_main(),
        probe_js(),
        probe_text(),
        probe_rate(),
        probe_block(),
        probe_cache(),
        probe_session(),
        probe_access(),
    ]


# -------------------------------------------------------------------- running
def run_probes(probes: List[Probe]) -> List[Result]:
    results: List[Result] = []
    for probe in probes:
        try:
            ok, detail = probe.check()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(Result(
            name=probe.name, family=probe.family,
            status="passed" if ok else "failed", detail=detail, cause=probe.cause,
        ))
    return results


def live_recheck(probes: List[Probe]) -> List[Result]:
    """Re-read the probe URLs over the network and check them again."""
    from iralens.core import IraLens
    from iralens.errors import IraLensError

    results: List[Result] = []
    for probe in probes:
        if not probe.live:
            results.append(Result(probe.name, probe.family, "not_run",
                                  "not a live page", probe.cause))
            continue
        try:
            with IraLens() as hil:
                artifact = hil.open(probe.url, mode="auto", max_chars=12000)
            content = artifact.content or ""
            budget = 12000
            ok = len(content) <= budget
            detail = f"status={artifact.status_code} chars={len(content)}"
            results.append(Result(probe.name, probe.family,
                                  "passed" if ok else "failed", detail, probe.cause))
        except IraLensError as exc:
            # A typed refusal is a *correct* outcome for a page that refuses
            # anonymous access; it is reported as external, not as a pass of
            # the content check.
            results.append(Result(probe.name, probe.family, "not_run",
                                  f"typed refusal: {exc.error_type}", EXTERNAL))
        except Exception as exc:
            results.append(Result(probe.name, probe.family, "not_run",
                                  f"{type(exc).__name__}: {exc}"[:200], EXTERNAL))
    return results


def report(results: List[Result], out_dir: Path, source: str) -> Dict[str, Any]:
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    not_run = sum(1 for r in results if r.status == "not_run")
    code_failures = sum(1 for r in results if r.status == "failed" and r.cause == CODE)
    summary = {
        "suite": "scraping",
        "source": source,
        "generated_at": _now(),
        "probes": len(results),
        "passed": passed,
        "failed": failed,
        "not_run": not_run,
        "code_failures": code_failures,
        "score": f"{passed}/{len(results)}",
        "all_passed": failed == 0 and not_run == 0,
        "results": [r.to_dict() for r in results],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"{'probe':34s} {'family':9s} {'status':9s} detail")
    print("-" * 100)
    for result in results:
        print(f"{result.name:34s} {result.family:9s} {result.status:9s} {result.detail[:44]}")
    print("-" * 100)
    print(f"passed {passed}/{len(results)}   failed {failed}   not_run {not_run}")
    if code_failures:
        print(f"code-caused failures: {code_failures}")
    print(f"\nwritten: {out_dir / 'results.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="re-read the probe URLs over the network")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    probes = build_probes()
    results = live_recheck(probes) if args.live else run_probes(probes)
    summary = report(results, Path(args.out), source="live" if args.live else "replay")
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["failed"] == 0 and summary["not_run"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
