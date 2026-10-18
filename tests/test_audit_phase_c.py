# -*- coding: utf-8 -*-
"""Phase C — Extraction, Resilience, Safety & Test-Readiness.

One test per defect in the error register (E-28 … E-35), each named after
the register entry so the two can be read side by side.

All offline: the network transports are fakes or captured fixtures.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "audit"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------- E-28
def test_E28_a_long_application_shell_is_detected():
    """A 2,119-character data shell is not a complete page."""
    from iralens.sources.web import looks_like_js_shell

    content = _fixture("read-Q0023.json")["output"]["content"]
    assert len(content) > 2000, "the fixture is the audit's shell"
    assert looks_like_js_shell(content) is True


def test_E28_real_articles_are_not_shells():
    from iralens.sources.web import looks_like_js_shell

    for name in ("read-Q0009.json", "read-Q0013.json", "read-Q0050.json"):
        content = _fixture(name)["output"]["content"]
        assert looks_like_js_shell(content) is False, name


# ------------------------------------------------------------------- E-29
def _wikipedia_shaped_page():
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
        "<div id='p-lang' class='vector-dropdown-content mw-language-variant'>"
        f"<div class='mw-language-variant-body'><ul>{langs}</ul></div></div></div></header>"
        "<div id='bodyContent'><div id='mw-content-text' class='mw-body-content'>"
        f"<div class='mw-parser-output'>{article}</div></div></div></main></div></body></html>")


def test_E29_the_main_content_is_selected():
    """The 111-language menu must not consume the character budget."""
    from iralens.extract import html_to_markdown

    selected = html_to_markdown(_wikipedia_shaped_page())
    assert "Language 5" not in selected
    assert "encyclopaedia lead" in selected
    # The selection is a no-op on a page without a content container.
    plain = ("<html><body><p>First sentence. Second sentence here. Third one too "
             "and more words.</p><p>More prose that goes on for a while so there "
             "is real text in this page.</p></body></html>")
    assert "First sentence" in html_to_markdown(plain)
    assert "More prose" in html_to_markdown(plain)


def test_E29_a_page_without_prose_keeps_its_text():
    from iralens.extract import html_to_markdown

    html = ("<html><body><div class='mw-parser-output'><p>Only one short line.</p>"
            "</div></body></html>")
    assert "Only one short line." in html_to_markdown(html)


# ------------------------------------------------------------------- E-30
def test_E30_the_rendered_text_collector_skips_script_and_style():
    source = (REPO / "iralens" / "_internal" / "browser_engine" / "vendor" / "obscura"
              / "crates" / "obscura-js" / "js" / "bootstrap.js").read_text(encoding="utf-8")
    index = source.find("function _collectInnerText")
    assert index != -1
    body = source[index:index + 4000]
    assert "SCRIPT" in body and "STYLE" in body
    assert "_innerTextSkippedTags" in body


def test_E30_script_source_does_not_leak_into_extracted_text():
    from iralens.extract import html_to_markdown

    html = ("<html><body><main><p>The visible article text.</p>"
            "<script>var leaked = 'secretCode();';</script>"
            "<style>.leaked { color: red; }</style></main></body></html>")
    text = html_to_markdown(html)
    assert "secretCode" not in text
    assert ".leaked" not in text
    assert "The visible article text." in text


# ------------------------------------------------------------------- E-31
def test_E31_a_non_element_node_can_be_asked_for_an_attribute():
    """The DOM stub must not crash a page script on a non-element node."""
    source = (REPO / "iralens" / "_internal" / "browser_engine" / "vendor" / "obscura"
              / "crates" / "obscura-js" / "js" / "bootstrap.js").read_text(encoding="utf-8")
    start = source.find("class Node {")
    node_class = source[start:start + 4000]
    assert "getAttribute(" in node_class, \
        "Node must expose a safe getAttribute so a page script cannot crash the render"


def test_E31_a_browser_failure_keeps_the_static_text():
    """A page whose renderer fails still yields the static text."""
    from iralens.core import IraLens
    from iralens.sources import web as web_mod

    home = tempfile.mkdtemp(prefix="iralens-phase-c-")
    os.environ["IRALENS_HOME"] = home
    original = web_mod.safe_urlopen

    class _Resp:
        def __init__(self, body, url):
            import io

            self._buf = io.BytesIO(body)
            self.url = url
            self.status = 200
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, n=-1):
            return self._buf.read(n if n >= 0 else len(self._buf.getvalue()))

        def getcode(self):
            return 200

        def geturl(self):
            return self.url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake(url, **kwargs):
        body = b"<html><body><p>" + b"content " * 60 + b"</p></body></html>"
        return _Resp(body, url)

    web_mod.safe_urlopen = _fake
    try:
        web_mod.HOST_RATE_LIMITER.min_interval = 0.0
        web_mod.HOST_RATE_LIMITER.default_min_interval = 0.0
        with IraLens() as hil:
            artifact = hil.open("https://example.org/x", mode="auto")
        assert artifact.content.strip()
        assert artifact.retrieval_method
    finally:
        web_mod.safe_urlopen = original
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------------------------- E-32
def test_E32_the_reader_remembers_a_dead_renderer():
    from iralens.reliability import TRANSPORT_HEALTH, TransportHealthMemory

    memory = TransportHealthMemory(failure_threshold=2)
    memory.record("example.org", "browser", ok=False)
    assert memory.is_dead("example.org", "browser") is False
    memory.record("example.org", "browser", ok=False)
    assert memory.is_dead("example.org", "browser") is True
    memory.record("example.org", "browser", ok=True)
    assert memory.is_dead("example.org", "browser") is False
    assert memory.known_good("example.org", "browser") is True
    assert TRANSPORT_HEALTH is not None


# ------------------------------------------------------------------- E-33
def test_E33_the_query_string_survives_and_the_effective_url_is_reported():
    from iralens.core import IraLens
    from iralens.sources import web as web_mod

    home = tempfile.mkdtemp(prefix="iralens-phase-c-")
    os.environ["IRALENS_HOME"] = home
    original = web_mod.safe_urlopen
    seen = {}

    class _Resp:
        def __init__(self, body, url):
            import io

            self._buf = io.BytesIO(body)
            self.url = url
            self.status = 200
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, n=-1):
            return self._buf.read(n if n >= 0 else len(self._buf.getvalue()))

        def getcode(self):
            return 200

        def geturl(self):
            return self.url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake(url, **kwargs):
        seen["url"] = url
        return _Resp(b"<html><body><p>page</p></body></html>", url)

    web_mod.safe_urlopen = _fake
    try:
        web_mod.HOST_RATE_LIMITER.min_interval = 0.0
        web_mod.HOST_RATE_LIMITER.default_min_interval = 0.0
        with IraLens() as hil:
            artifact = hil.open("https://example.org/list?page=2&q=solar", mode="static")
        assert seen["url"].endswith("?page=2&q=solar"), seen["url"]
        assert artifact.requested_url.endswith("?page=2&q=solar")
        assert artifact.effective_url and "page=2" in artifact.effective_url
        assert artifact.metadata.get("redirect_chain")
    finally:
        web_mod.safe_urlopen = original
        shutil.rmtree(home, ignore_errors=True)


def test_E33_the_artifact_carries_an_effective_url():
    from iralens.model import Artifact

    artifact = Artifact(title="t", url="https://example.org/x", source="web",
                        requested_url="https://example.org/x",
                        final_url="https://example.org/y",
                        effective_url="https://example.org/y")
    data = artifact.to_dict()
    assert data["effective_url"] == "https://example.org/y"


# ------------------------------------------------------------------- E-34
def test_E34_the_acceptance_runner_is_in_the_repository():
    script = REPO / "benchmarks" / "acceptance_17.py"
    assert script.is_file(), "the 17-check acceptance script must live in the repo"
    out = subprocess.run([sys.executable, str(script), "--json"],
                         capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    payload = json.loads(out.stdout)
    assert payload["total"] == 17
    assert payload["passed"] == 17, [c for c in payload["checks"] if not c["passed"]]
    assert payload["all_passed"] is True


def test_E34_the_scraping_runner_is_in_the_repository():
    script = REPO / "benchmarks" / "run_scraping.py"
    assert script.is_file()
    out_dir = Path(tempfile.mkdtemp(prefix="iralens-scraping-out-"))
    try:
        out = subprocess.run([sys.executable, str(script), "--out", str(out_dir)],
                             capture_output=True, text=True, cwd=str(REPO))
        assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
        payload = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
        assert payload.get("failed") == 0, payload
        assert payload.get("not_run") == 0, payload
        assert payload.get("passed") == payload.get("probes")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_E34_the_3000_query_runner_is_in_the_repository():
    script = REPO / "benchmarks" / "run_audit3000.py"
    assert script.is_file()
    corpus = REPO / "benchmarks" / "audit3000" / "queries.jsonl"
    assert corpus.is_file()
    lines = [l for l in corpus.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3000
    out = subprocess.run([sys.executable, str(script), "--self-test"],
                         capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "self-test: ok" in out.stdout


def test_E34_the_corpus_is_hash_verified():
    import hashlib

    corpus = REPO / "benchmarks" / "audit3000" / "queries.jsonl"
    expected = (REPO / "benchmarks" / "audit3000" / "dataset.sha256").read_text(
        encoding="utf-8").split()[0]
    actual = hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert actual == expected


# ------------------------------------------------------------------- E-35
def test_E35_the_documentation_matches_the_measured_behaviour():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    limitations = (REPO / "KNOWN_LIMITATIONS.md").read_text(encoding="utf-8")
    # The runners must be discoverable from the documentation.
    assert "acceptance_17" in readme or "acceptance_17" in limitations
    assert "run_audit3000" in readme or "run_audit3000" in limitations
    assert "run_scraping" in readme or "run_scraping" in limitations


def test_E35_the_known_limitations_name_what_is_still_external():
    limitations = (REPO / "KNOWN_LIMITATIONS.md").read_text(encoding="utf-8")
    low = limitations.lower()
    assert "captcha" in low, "the provider-side captcha must still be documented"
    assert "external" in low or "provider" in low
