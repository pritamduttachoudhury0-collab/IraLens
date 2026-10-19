# -*- coding: utf-8 -*-
"""End-to-end integration tests for the internally vendored IraLens browser engine.

All browser tests run against a real local HTTP server (`ThreadingHTTPServer`)
and execute the real `iralens-browser-engine` subprocess + V8 runtime without
mocking browser responses.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from iralens import IraLens, errors
from iralens._internal.browser_engine import (
    PACKAGE_DIR,
    UPSTREAM_BASE_TAG,
    UPSTREAM_REVISION,
    VENDOR_OBSCURA_DIR,
)
from iralens.config import Config
from iralens.engine.install import engine_status, install_engine
from iralens.engine.locate import bundled_engine_path, find_engine


def _cfg(tmp_path: Path, **overrides: Any) -> Config:
    cfg = Config(tmp_path / "config.yaml")
    cfg._data.update(overrides)
    return cfg


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    server_version = "IraLensFixtureHTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # Keep test output clean

    def _send(
        self,
        status: int,
        body: str,
        *,
        content_type: str = "text/html; charset=utf-8",
        headers: Dict[str, str] | None = None,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        try:
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path == "/index":
            self._send(
                200,
                """<!DOCTYPE html>
<html>
<head><title>IraLens Local Fixture Home</title></head>
<body>
  <header><h1>Welcome to IraLens Fixture</h1></header>
  <main>
    <p class="lead">Fast, deterministic V8 + HTML5 DOM rendering.</p>
    <ul>
      <li class="feature">Navigation</li>
      <li class="feature">Extraction</li>
      <li class="feature">Interaction</li>
    </ul>
    <a id="main-cta" data-action="launch" href="/spa">Open SPA</a>
    <a id="ext-link" href="https://example.org/docs">External Docs</a>
  </main>
</body>
</html>""",
            )
            return

        if path == "/static/app.js":
            self._send(
                200,
                """
window.__EXTERNAL_BUNDLE_LOADED__ = true;
window.renderAppDashboard = function(rootEl) {
  rootEl.innerHTML = '<h2>Rendered Dashboard</h2><p id="metric" data-status="ready">Token count: 42</p><p>Client-side application initialized inside the isolated V8 runtime with full DOM, CSSOM, and event dispatch support.</p><span class="badge">Live V8</span>';
  document.title = 'SPA Dashboard Ready';
};
""",
                content_type="application/javascript; charset=utf-8",
            )
            return

        if path == "/spa":
            self._send(
                200,
                """<!DOCTYPE html>
<html>
<head>
  <title>Loading App...</title>
  <script src="/static/app.js"></script>
</head>
<body>
  <div id="app">Please enable JavaScript to view this application.</div>
  <script>
    document.addEventListener("DOMContentLoaded", function() {
      var root = document.getElementById("app");
      if (window.renderAppDashboard) {
        window.renderAppDashboard(root);
      }
    });
  </script>
</body>
</html>""",
            )
            return

        if path == "/interactive":
            self._send(
                200,
                """<!DOCTYPE html>
<html>
<head><title>Interactive Form & Counter</title></head>
<body>
  <h1>Control Panel</h1>
  <div>
    <span id="count-val">0</span>
    <button id="inc-btn" type="button">Increment Counter</button>
  </div>
  <form id="profile-form" method="POST" action="/submit">
    <label for="username-input">Username</label>
    <input id="username-input" name="username" type="text" value="" required />
    <label for="role-select">Role</label>
    <select id="role-select" name="role">
      <option value="viewer">Viewer</option>
      <option value="researcher">Researcher</option>
      <option value="admin">Admin</option>
    </select>
    <button id="submit-btn" type="submit">Save Profile</button>
  </form>
  <script>
    var count = 0;
    var btn = document.getElementById("inc-btn");
    var span = document.getElementById("count-val");
    btn.addEventListener("click", function() {
      count += 1;
      span.textContent = String(count);
    });
  </script>
</body>
</html>""",
            )
            return

        if path == "/redirect-start":
            self._send(301, "", headers={"Location": "/redirect-mid"})
            return

        if path == "/redirect-mid":
            self._send(302, "", headers={"Location": "/final-destination"})
            return

        if path == "/final-destination":
            self._send(
                200,
                """<!DOCTYPE html>
<html>
<head><title>Final Destination Page</title></head>
<body><h1>Arrived at Final Destination</h1><p>Redirect chain completed.</p></body>
</html>""",
            )
            return

        if path == "/redirect-to-metadata":
            self._send(
                302,
                "",
                headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            )
            return

        if path == "/subresource-metadata":
            self._send(
                200,
                """<!DOCTYPE html>
<html>
<head><title>Page With Hostile Subresource</title></head>
<body>
  <h1>Legitimate Content</h1>
  <script src="http://169.254.169.254/latest/meta-data/iam/"></script>
</body>
</html>""",
            )
            return

        if path == "/missing-404":
            self._send(
                404,
                "<!DOCTYPE html><html><head><title>404 Not Found</title></head><body><h1>404 Not Found</h1></body></html>",
            )
            return

        if path == "/error-500":
            self._send(
                500,
                "<!DOCTYPE html><html><head><title>500 Server Error</title></head><body><h1>Internal Error</h1></body></html>",
            )
            return

        if path == "/set-session":
            self._send(
                200,
                """<!DOCTYPE html>
<html>
<head><title>Session Initialized</title></head>
<body>
  <h1>Session Active</h1>
  <script>
    document.cookie = "client_theme=dark; path=/";
    localStorage.setItem("saved_workspace", "iralens-lab");
  </script>
</body>
</html>""",
                headers={"Set-Cookie": "server_sid=sec_998877; Path=/; HttpOnly"},
            )
            return

        if path == "/echo-session":
            cookie_hdr = self.headers.get("Cookie", "")
            self._send(
                200,
                f"""<!DOCTYPE html>
<html>
<head><title>Session Echo</title></head>
<body>
  <p id="server-cookies">{cookie_hdr}</p>
  <p id="js-cookies"></p>
  <p id="ls-val"></p>
  <script>
    document.getElementById("js-cookies").textContent = document.cookie;
    document.getElementById("ls-val").textContent = localStorage.getItem("saved_workspace") || "";
  </script>
</body>
</html>""",
            )
            return

        if path == "/slow":
            time.sleep(2.5)
            self._send(200, "<html><head><title>Slow</title></head><body>Slow response</body></html>")
            return

        self._send(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params = urllib.parse.parse_qs(raw_body)
        if self.path == "/submit":
            user = params.get("username", [""])[0]
            role = params.get("role", [""])[0]
            self._send(
                200,
                f"""<!DOCTYPE html>
<html>
<head><title>Submission Complete</title></head>
<body>
  <h1>Profile Saved</h1>
  <p id="result-summary">Submitted user={user} role={role}</p>
</body>
</html>""",
            )
            return
        self._send(404, "Not Found")


@pytest.fixture(scope="module")
def fixture_server() -> Tuple[str, http.server.ThreadingHTTPServer]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def allow_local_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRALENS_ALLOW_PRIVATE_NETWORK", "1")


def test_vendored_obscura_provenance_and_assets() -> None:
    """Verify vendored Obscura source tree, license, and provenance files exist."""
    assert VENDOR_OBSCURA_DIR.is_dir()
    assert (VENDOR_OBSCURA_DIR / "LICENSE").is_file()
    assert (VENDOR_OBSCURA_DIR / "Cargo.toml").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-js" / "js" / "bootstrap.js").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-js" / "js" / "indexed_db.js").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-js" / "src" / "markdown.rs").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-ssrf" / "src" / "lib.rs").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-dom" / "src" / "lib.rs").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-net" / "src" / "lib.rs").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-browser" / "src" / "lib.rs").is_file()
    assert (VENDOR_OBSCURA_DIR / "crates" / "obscura-mcp" / "src" / "lib.rs").is_file()

    prov = (PACKAGE_DIR / "UPSTREAM_PROVENANCE.md").read_text(encoding="utf-8")
    notices = (PACKAGE_DIR / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert UPSTREAM_REVISION in prov
    assert UPSTREAM_BASE_TAG in prov
    assert "Apache-2.0" in notices


def test_bundled_engine_discovery_and_install_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify discovery and installation work completely offline from the bundled engine."""
    monkeypatch.setenv("IRALENS_ENGINE_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("IRALENS_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.delenv("IRALENS_ENGINE_PATH", raising=False)

    # 1. Bundled engine is found immediately even when cache is empty
    bundled = bundled_engine_path()
    assert bundled is not None and bundled.is_file()
    discovered = find_engine()
    assert discovered is not None and discovered.is_file()

    # 2. install_engine materializes from the vendored package with zero network calls
    report: Dict[str, object] = {}
    installed = install_engine(target_dir=tmp_path / "installed-engine", report=report)
    assert installed.is_file()
    assert os.access(installed, os.X_OK)

    st = engine_status()
    assert st["installed"] is True
    assert st["bundled"] is True
    assert st["upstream_revision"] == UPSTREAM_REVISION
    assert st["executable"] is True


def test_doctor_reports_integrated_browser_ready(tmp_path: Path) -> None:
    """Verify hil.doctor() reports browser status ok and never leaks upstream names."""
    with IraLens(_cfg(tmp_path)) as hil:
        doc = hil.doctor()
        assert doc["browser"]["status"] == "ok"
        blob = json.dumps(doc).lower()
        assert "obscura" not in blob
        assert "agent-reach" not in blob


def test_unavailable_or_misconfigured_engine_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_server: Tuple[str, Any]
) -> None:
    """When the native engine is disabled or misconfigured, IraLens fails with EngineUnavailableError."""
    base_url, _ = fixture_server
    monkeypatch.setenv("IRALENS_ALLOW_PRIVATE_NETWORK", "1")
    monkeypatch.setenv("IRALENS_DISABLE_BUNDLED_ENGINE", "1")
    monkeypatch.setenv("IRALENS_ENGINE_CACHE_DIR", str(tmp_path / "no-cache"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.delenv("IRALENS_ENGINE_PATH", raising=False)

    assert find_engine() is None
    with IraLens(_cfg(tmp_path)) as hil:
        with pytest.raises(errors.EngineUnavailableError) as exc_info:
            hil.open(f"{base_url}/index", mode="browser")
        assert exc_info.value.error_type == "browser_engine_unavailable"


def test_real_browser_basic_navigation_and_extraction(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Test real navigation, title/URL/content extraction, links, count, attribute, and schema extract."""
    base_url, _ = fixture_server
    target = f"{base_url}/index"

    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        art = hil.open(target, mode="browser")
        assert art.title == "IraLens Local Fixture Home"
        assert art.url == target
        assert art.requested_url == target
        assert art.final_url == target
        assert art.status_code == 200
        assert "text/html" in (art.content_type or "")
        assert art.retrieval_method == "browser"
        assert art.untrusted is True
        assert "Welcome to IraLens Fixture" in art.content
        assert "Fast, deterministic V8 + HTML5 DOM rendering." in art.content

        links = hil.links()
        assert any(lnk.get("href") == f"{base_url}/spa" for lnk in links)

        internal_links = hil.links(internal_only=True)
        assert all("127.0.0.1" in lnk.get("href", "") for lnk in internal_links)

        assert hil.count("li.feature") == {"selector": "li.feature", "count": 3}
        assert hil.attribute("#main-cta", "data-action") == "launch"

        extracted = hil.extract({"heading": "h1", "features": "li.feature"})
        assert extracted["heading"] == "Welcome to IraLens Fixture"
        assert extracted["features"] == ["Navigation", "Extraction", "Interaction"]


def test_real_browser_stealth_reaches_the_engine_subprocess(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """`stealth: true` must survive the trip through the driver.

    The flag crosses two processes (the driver and the engine subprocess),
    so a test that only calls the engine in-process would not notice it
    being dropped on the way.
    """
    base_url, _ = fixture_server
    with IraLens(_cfg(tmp_path, allow_private=True, stealth=True)) as hil:
        hil.open(f"{base_url}/index", mode="browser")
        status = hil.engine().stealth_status()
        assert status["requested"] is True
        assert status["supported"] is True
        assert status["active"] is True
        # The page agrees with the identity the engine claims to have.
        seen = hil.engine().call("browser_evaluate", {
            "expression": (
                "JSON.stringify({ua: navigator.userAgent, "
                "platform: navigator.platform, "
                "screen: [screen.width, screen.height].join('x'), "
                "cores: navigator.hardwareConcurrency, "
                "memory: navigator.deviceMemory})"
            ),
        })
        assert "Chrome" in seen["ua"]
        assert seen["platform"] == "Win32"
        assert seen["cores"] in (4, 8, 10, 12, 16)
        assert seen["memory"] in (4, 8)
        assert "x" in seen["screen"]


def test_real_browser_without_stealth_keeps_the_default_identity(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Stealth is opt-in: not asking for it must not change the engine."""
    base_url, _ = fixture_server
    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        hil.open(f"{base_url}/index", mode="browser")
        status = hil.engine().stealth_status()
        assert status["requested"] is False
        assert status["active"] is False


def test_real_browser_js_dependent_rendering_and_auto_fallback(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Test client-side JS rendering (including external <script src>) and automatic JS-shell fallback."""
    base_url, _ = fixture_server
    spa_url = f"{base_url}/spa"

    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        # 1. Static mode sees only the unrendered app shell
        static_art = hil.open(spa_url, mode="static")
        assert "enable JavaScript" in static_art.content
        assert "Rendered Dashboard" not in static_art.content

        # 2. Auto mode detects the JS shell and automatically renders in the V8 browser engine
        auto_art = hil.open(spa_url, mode="auto")
        assert auto_art.retrieval_method == "browser"
        assert auto_art.title == "SPA Dashboard Ready"
        assert "Rendered Dashboard" in auto_art.content
        assert "Token count: 42" in auto_art.content
        assert auto_art.provenance.get("fallback", {}).get("result") == "browser_text"


def test_real_browser_javascript_evaluation(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Test JavaScript evaluation inside the active page V8 isolate."""
    base_url, _ = fixture_server
    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        hil.navigate(f"{base_url}/spa")
        assert hil.evaluate_js("window.__EXTERNAL_BUNDLE_LOADED__") is True
        assert hil.evaluate_js("document.querySelector('#metric').getAttribute('data-status')") == "ready"
        computed = hil.evaluate_js(
            "({title: document.title, badges: document.querySelectorAll('.badge').length, sum: [10, 20, 12].reduce((a,b) => a+b, 0)})"
        )
        assert computed == {"title": "SPA Dashboard Ready", "badges": 1, "sum": 42}


def test_real_browser_click_and_form_interaction(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Test accessibility snapshot [ref=N], button click handlers, form detection, fill, and POST submit."""
    base_url, _ = fixture_server
    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        hil.navigate(f"{base_url}/interactive")

        snap = hil.snapshot()
        assert "Control Panel" in snap
        assert "[ref=" in snap

        # Click the counter button twice and verify DOM mutation via JS event listener
        hil.click("#inc-btn")
        hil.click("#inc-btn")
        assert str(hil.evaluate_js("document.getElementById('count-val').textContent")) == "2"

        # Detect forms
        forms = hil.forms_detect()
        assert isinstance(forms, list) and len(forms) == 1
        assert forms[0]["method"] == "POST"

        # Fill form inputs and submit
        hil.fill("#username-input", "alice_v8")
        hil.select_option("#role-select", "researcher")
        hil.click("#submit-btn")

        # Verify POST navigation completed and rendered response
        current = hil.engine().current_url_title()
        assert current["url"].endswith("/submit")
        assert current["title"] == "Submission Complete"
        md = hil.page_markdown()
        assert "Submitted user=alice_v8 role=researcher" in md


def test_real_browser_redirects_and_missing_page_honesty(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Test multi-hop HTTP redirects (301 -> 302 -> 200) and honest failure on 404/500 pages."""
    base_url, _ = fixture_server
    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        # 1. Multi-hop redirect
        req_url = f"{base_url}/redirect-start"
        art = hil.open(req_url, mode="browser")
        assert art.requested_url == req_url
        assert art.final_url == f"{base_url}/final-destination"
        assert art.url == f"{base_url}/final-destination"
        assert art.title == "Final Destination Page"
        assert art.status_code == 200
        assert art.metadata["redirected"] is True
        assert len(art.metadata["redirect_chain"]) == 3
        assert "Arrived at Final Destination" in art.content

        # 2. Missing page (HTTP 404) must NOT be misrepresented as a successful Artifact
        with pytest.raises(errors.PageUnavailableError) as exc_404:
            hil.open(f"{base_url}/missing-404", mode="browser")
        assert exc_404.value.error_type == "page_unavailable"
        assert "404" in (exc_404.value.detail or "") or "404" in exc_404.value.message

        # 3. Server error (HTTP 500) must also fail honestly
        with pytest.raises(errors.PageUnavailableError) as exc_500:
            hil.open(f"{base_url}/error-500", mode="browser")
        assert exc_500.value.error_type == "page_unavailable"


def test_real_browser_ssrf_and_redirect_security(
    tmp_path: Path, fixture_server: Tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test SSRF protection on direct navigation and redirect-to-metadata hops."""
    base_url, _ = fixture_server

    # 1. Without IRALENS_ALLOW_PRIVATE_NETWORK, loopback & metadata URLs are rejected
    monkeypatch.delenv("IRALENS_ALLOW_PRIVATE_NETWORK", raising=False)
    with IraLens(_cfg(tmp_path)) as hil:
        for blocked_url in (
            f"{base_url}/index",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
        ):
            with pytest.raises(errors.SecurityBlockedError):
                hil.navigate(blocked_url)

    # 2. Even when private loopback is enabled for local fixtures, a redirect hop
    #    to cloud metadata (169.254.169.254) is blocked by Obscura's per-hop SSRF check
    monkeypatch.setenv("IRALENS_ALLOW_PRIVATE_NETWORK", "1")
    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        with pytest.raises(errors.SecurityBlockedError):
            hil.navigate(f"{base_url}/redirect-to-metadata")


def test_real_browser_blocks_ssrf_in_subresources(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """A page's own script tags must not reach internal addresses.

    Navigation-level SSRF checks are not enough on their own: a hostile page
    can name an internal host in a subresource URL and let the *browser* make
    the request instead of navigating to it. The block must be recorded, and
    the page must still render -- a blocked subresource is not a failed read.
    """
    base_url, _ = fixture_server
    hil = IraLens(_cfg(tmp_path, allow_private=True))
    try:
        outcome = hil.engine().navigate(f"{base_url}/subresource-metadata")
    finally:
        hil.close()

    rendered = outcome if isinstance(outcome, dict) else outcome.to_dict()
    warnings = " ".join(str(w) for w in rendered.get("warnings", []))
    assert "blocked_subresource" in warnings
    assert "169.254.169.254" in warnings
    # The legitimate part of the page still came through.
    assert rendered.get("ok") is not False


def test_real_browser_timeouts_and_cleanup(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Test V8 infinite-loop script timeout, slow HTTP timeout, and subprocess cleanup."""
    base_url, _ = fixture_server
    hil = IraLens(_cfg(tmp_path, allow_private=True))
    try:
        hil.navigate(f"{base_url}/index")
        assert hil.engine().running is True

        # 1. V8 script execution timeout on infinite loop
        with pytest.raises(errors.OperationTimeoutError):
            hil.engine().call("browser_evaluate", {"expression": "while(true){}", "timeout": 0.4}, timeout=5)

        # 2. Engine recovers and remains usable after script timeout
        assert hil.evaluate_js("1 + 1") == 2

        # 3. Navigation timeout on slow endpoint
        with pytest.raises(errors.OperationTimeoutError):
            hil.engine().navigate(f"{base_url}/slow", timeout=1)
    finally:
        hil.close()
        assert hil._engine is None or not hil._engine.running


def test_real_browser_cookies_session_persistence_and_tabs(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Test RFC 6265 cookies (HttpOnly + JS), localStorage persistence across sessions, and multi-tab management."""
    base_url, _ = fixture_server
    storage_dir = tmp_path / "browser-storage"

    # Session 1: visit /set-session which sets an HttpOnly server cookie, a JS cookie, and localStorage
    with IraLens(
        _cfg(tmp_path, allow_private=True, storage_dir=str(storage_dir))
    ) as hil1:
        hil1.navigate(f"{base_url}/set-session")
        hil1.navigate(f"{base_url}/echo-session")

        # Server received both HttpOnly cookie and JS cookie
        srv_cookies = hil1.evaluate_js("document.getElementById('server-cookies').textContent")
        assert "server_sid=sec_998877" in srv_cookies
        assert "client_theme=dark" in srv_cookies

        # JS document.cookie only sees non-HttpOnly cookie
        js_cookies = hil1.evaluate_js("document.getElementById('js-cookies').textContent")
        assert "client_theme=dark" in js_cookies
        assert "server_sid" not in js_cookies

        # localStorage persisted across same-origin navigation
        ls_val = hil1.evaluate_js("document.getElementById('ls-val').textContent")
        assert ls_val == "iralens-lab"

        # Multi-tab lifecycle
        new_tab = hil1.tab_new(f"{base_url}/index")
        assert new_tab["tab_id"] == "tab-2"
        tabs = hil1.tab_list()
        assert len(tabs) == 2
        hil1.tab_switch("tab-1")
        assert hil1.engine().current_url_title()["title"] == "Session Echo"
        hil1.tab_close("tab-2")
        assert len(hil1.tab_list()) == 1

    # Session 2: new IraLens instance pointing at the same storage_dir restores cookies + localStorage
    with IraLens(
        _cfg(tmp_path, allow_private=True, storage_dir=str(storage_dir))
    ) as hil2:
        hil2.navigate(f"{base_url}/echo-session")
        srv_cookies_2 = hil2.evaluate_js("document.getElementById('server-cookies').textContent")
        assert "server_sid=sec_998877" in srv_cookies_2
        assert "client_theme=dark" in srv_cookies_2
        ls_val_2 = hil2.evaluate_js("document.getElementById('ls-val').textContent")
        assert ls_val_2 == "iralens-lab"


def test_unified_result_schema_across_modes(
    tmp_path: Path, allow_local_fixture: None, fixture_server: Tuple[str, Any]
) -> None:
    """Verify static HTTP, browser render, and auto mode all emit the unified Artifact schema."""
    base_url, _ = fixture_server
    required_keys = {
        "title",
        "url",
        "requested_url",
        "final_url",
        "source",
        "kind",
        "content",
        "content_format",
        "metadata",
        "retrieval_method",
        "retrieved_at",
        "untrusted",
        "status_code",
        "content_type",
        "provenance",
    }

    with IraLens(_cfg(tmp_path, allow_private=True)) as hil:
        static_dict = hil.open(f"{base_url}/index", mode="static").to_dict()
        browser_dict = hil.open(f"{base_url}/index", mode="browser").to_dict()
        auto_spa_dict = hil.open(f"{base_url}/spa", mode="auto").to_dict()

        for payload in (static_dict, browser_dict, auto_spa_dict):
            missing = required_keys - set(payload.keys())
            assert not missing, f"Missing unified schema keys: {missing}"
            assert payload["status_code"] == 200
            assert "text/html" in payload["content_type"]
            assert payload["untrusted"] is True
            serialized = json.dumps(payload).lower()
            assert "obscura" not in serialized
            assert "agent-reach" not in serialized
