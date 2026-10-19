# -*- coding: utf-8 -*-
"""The stealth tier: one coherent browser identity, and honest challenges.

Anti-bot systems compare the request, the response headers and what the
page's JavaScript can observe. A client that claims to be Chrome 145 on
Windows but sends ``Connection: close``, no ``sec-ch-ua`` and a
``deviceMemory`` of 2 is not "stealthy" -- it is inconsistent, and
inconsistency is what gets flagged.

These tests pin the properties that matter:

* the identity is **coherent** -- the header set, the client hints and the
  page's own view of itself all say the same thing;
* the identity is **stable** -- a second page load reports the same screen,
  the same GPU and the same canvas, because a browser does not change its
  fingerprint between pages;
* the challenge handling is **honest** -- a challenge is detected, a solved
  token is entered, and an unsolved challenge is reported rather than
  returned as if it were content.

Everything here is offline: the engine talks to a local HTTP server.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from iralens._internal.browser_engine import captcha as captcha_mod
from iralens._internal.browser_engine import mcp_server
from iralens._internal.browser_engine.runtime import ObscuraNativeEngine
from iralens._internal.browser_engine.stealth import (
    DEFAULT_STEALTH_UA,
    StealthProfile,
    brand_list,
    fetch_site,
    load_or_create_profile,
    save_profile,
)
from iralens._internal.browser_engine.transport import (
    HttpTransport,
    accept_encoding_value,
    supported_codings,
)
from iralens.errors import VerificationRequiredError


# --------------------------------------------------------------------- server
class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "IraLensStealthFixture/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/captcha"):
            body = (
                b"<!doctype html><html><head><title>Verify</title></head><body>"
                b'<div class="g-recaptcha" data-sitekey="abcdefghij1234567890"></div>'
                b'<textarea name="g-recaptcha-response"></textarea>'
                b'<script src="https://www.google.com/recaptcha/api.js"></script>'
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/badge"):
            # A normal article that happens to carry a reCAPTCHA badge, the
            # way many sites do. The article is readable, so the badge must
            # not make the page unreadable.
            article = ("<p>" + ("Readable article text. " * 200) + "</p>").encode("utf-8")
            body = (
                b"<!doctype html><html><head><title>Article</title></head><body>"
                b'<h1>An article</h1>' + article +
                b'<div class="g-recaptcha" data-sitekey="abcdefghij1234567890"></div>'
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/gzip"):
            import gzip

            body = gzip.compress(b"<!doctype html><html><body>compressed</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = (
            b"<!doctype html><html><head><title>Fixture</title></head><body>"
            b"<h1>Fixture page</h1><p>Some readable text for the agent.</p>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


# -------------------------------------------------------------------- profile
def test_profile_is_derived_from_the_user_agent():
    profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=7)
    assert profile.user_agent == DEFAULT_STEALTH_UA
    assert profile.platform == "Win32"
    assert profile.ua_platform == "Windows"
    assert profile.chrome_major == 145
    assert profile.family == "windows"


def test_profile_platform_matches_a_macos_user_agent():
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
    profile = StealthProfile.derive(ua, seed=11)
    assert profile.platform == "MacIntel"
    assert profile.ua_platform == "macOS"


def test_profile_values_are_desktop_plausible():
    """A phone-sized core count or 256 MB of RAM is a fingerprint."""
    for seed in range(25):
        profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=seed)
        assert profile.hardware_concurrency in (4, 8, 10, 12, 16)
        assert profile.device_memory in (4, 8)
        assert profile.max_touch_points == 0
        assert profile.screen_width >= 1200
        assert profile.inner_height < profile.screen_height
        assert profile.outer_height <= profile.screen_height


def test_brand_list_matches_the_engines_own_algorithm():
    """The header and the page must report the same brands, in order.

    The upstream engine builds `navigator.userAgentData.brands` with a
    specific GREASE character and permutation. The `Sec-CH-UA` header is
    built here from the same algorithm, so the two cannot drift apart.
    """
    brands = brand_list(145)
    assert [b["brand"] for b in brands] == [
        "Not:A-Brand", "Google Chrome", "Chromium",
    ]
    assert [b["version"] for b in brands] == ["99", "145", "145"]


def test_the_brand_order_varies_but_the_grease_pair_does_not():
    """Chrome randomizes the brand order per session, not the GREASE pair."""
    orders = {tuple(b["brand"] for b in brand_list(145, seed)) for seed in range(40)}
    assert len(orders) > 1
    for seed in range(40):
        brands = brand_list(145, seed)
        grease = [b for b in brands if b["brand"].startswith("Not")][0]
        assert grease["version"] == "99"


def test_sec_ch_ua_renders_the_brand_list():
    profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=3)
    value = profile.sec_ch_ua
    assert value.count('"') == len(profile.brands) * 4
    for brand in profile.brands:
        assert f'"{brand["brand"]}";v="{brand["version"]}"' in value


def test_navigation_headers_are_in_chrome_order():
    profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=5)
    names = [name for name, _ in profile.navigation_headers(
        url="https://example.com/page", accept_encoding="gzip, deflate"
    )]
    assert names == [
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "Upgrade-Insecure-Requests",
        "User-Agent",
        "Accept",
        "Sec-Fetch-Site",
        "Sec-Fetch-Mode",
        "Sec-Fetch-User",
        "Sec-Fetch-Dest",
        "Accept-Encoding",
        "Accept-Language",
        "Priority",
    ]


def test_a_navigation_with_a_referer_is_not_a_typed_url():
    """Chrome sends `Sec-Fetch-Site: none` only for a URL the user typed.

    Sending a Referer next to `Sec-Fetch-Site: none` contradicts itself.
    """
    profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=5)
    typed = dict(profile.navigation_headers(
        url="https://example.com/page", accept_encoding="gzip"
    ))
    assert typed["Sec-Fetch-Site"] == "none"
    followed = dict(profile.navigation_headers(
        url="https://example.com/next", accept_encoding="gzip",
        referer="https://example.com/page",
    ))
    assert followed["Sec-Fetch-Site"] == "same-origin"
    assert followed["Referer"] == "https://example.com/page"
    cross = dict(profile.navigation_headers(
        url="https://other.test/next", accept_encoding="gzip",
        referer="https://example.com/page",
    ))
    assert cross["Sec-Fetch-Site"] == "cross-site"


def test_subresource_headers_differ_from_navigation_headers():
    profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=5)
    headers = dict(profile.subresource_headers(
        "https://example.com/app.js",
        resource_type="script",
        accept_encoding="gzip",
        referer="https://example.com/",
    ))
    assert headers["Sec-Fetch-Dest"] == "script"
    assert headers["Sec-Fetch-Mode"] == "no-cors"
    assert headers["Sec-Fetch-Site"] == "same-origin"
    assert headers["Referer"] == "https://example.com/"
    # A subresource is not a navigation: no upgrade-insecure-requests.
    assert "Upgrade-Insecure-Requests" not in headers


def test_accept_language_matches_the_profile_languages():
    profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=9)
    language, secondary = profile.languages[0], profile.languages[1]
    assert profile.accept_language == f"{language},{secondary};q=0.9"
    assert profile.language == language


def test_fetch_site_distinguishes_origin_site_and_cross_site():
    assert fetch_site("https://a.example/x", "https://a.example/") == "same-origin"
    assert fetch_site("https://www.example.com/x", "https://example.com/") == "same-site"
    assert fetch_site("https://other.test/x", "https://example.com/") == "cross-site"
    assert fetch_site("https://example.com/x", None) == "none"


def test_profile_survives_a_restart(tmp_path: Path):
    first = load_or_create_profile(tmp_path)
    second = load_or_create_profile(tmp_path)
    assert first.to_dict() == second.to_dict()


def test_profile_is_pinned_to_the_storage_directory(tmp_path: Path):
    first = load_or_create_profile(tmp_path)
    other = load_or_create_profile(tmp_path / "other")
    assert first.seed != other.seed


def test_a_new_user_agent_replaces_the_stored_identity(tmp_path: Path):
    load_or_create_profile(tmp_path)
    changed = load_or_create_profile(
        tmp_path, user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    )
    assert changed.platform == "Linux x86_64"


def test_a_corrupt_profile_file_does_not_break_the_engine(tmp_path: Path):
    (tmp_path / "stealth_profile.json").write_text("{not json", encoding="utf-8")
    profile = load_or_create_profile(tmp_path)
    assert profile.user_agent == DEFAULT_STEALTH_UA


def test_saved_profile_round_trips(tmp_path: Path):
    profile = StealthProfile.derive(DEFAULT_STEALTH_UA, seed=17)
    save_profile(tmp_path, profile)
    assert load_or_create_profile(tmp_path).to_dict() == profile.to_dict()


# ------------------------------------------------------------------ transport
def test_accept_encoding_only_advertises_decodable_codecs():
    codings = supported_codings()
    assert "gzip" in codings and "deflate" in codings
    assert accept_encoding_value() == ", ".join(codings)


def test_transport_sends_headers_in_the_given_order(server: str):
    received: List[Tuple[str, str]] = []

    class _Recorder(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            received.extend(self.headers.items())
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        transport = HttpTransport()
        transport.open(
            f"http://127.0.0.1:{httpd.server_address[1]}/x",
            headers=[("User-Agent", "UA/1"), ("Accept", "*/*"), ("X-Last", "1")],
        )
        transport.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    names = [name for name, _ in received]
    assert names.index("User-Agent") < names.index("Accept") < names.index("X-Last")


def test_transport_decodes_gzip(server: str):
    transport = HttpTransport()
    response = transport.open(f"{server}/gzip")
    transport.close()
    assert b"compressed" in response.body


def test_the_tls_profile_matches_the_claimed_chrome_version():
    """The TLS fingerprint carries a Chrome version of its own.

    If it disagrees with the user agent, the client contradicts itself in
    a way a detector can see. The mapping depends on the optional
    ``curl_cffi`` backend: without it the honest answer is the generic
    ``"chrome"`` profile; with it the closest available version is chosen.
    Both states are part of the contract and are tested explicitly.
    """
    import importlib.util
    import typing

    from iralens._internal.browser_engine.transport import _curl_impersonation_target

    try:
        from curl_cffi.requests.impersonate import (  # type: ignore
            BrowserTypeLiteral,
        )

        available = [str(item) for item in typing.get_args(BrowserTypeLiteral)]
    except Exception:
        available = []
    backend_installed = importlib.util.find_spec("curl_cffi") is not None

    if not backend_installed or not available:
        # No optional backend: the transport must fall back to the generic
        # profile name rather than inventing a version-specific one.
        assert _curl_impersonation_target(145) == "chrome"
        assert _curl_impersonation_target(None) == "chrome"
        return

    # An exact match must be preferred when the backend offers one.
    versions = sorted(
        int(name[len("chrome"):])
        for name in available
        if name.startswith("chrome")
        and name[len("chrome"):].isdigit()
    )
    assert versions, "curl_cffi advertises no versioned chrome profiles"
    exact = versions[-1]
    assert _curl_impersonation_target(exact) == f"chrome{exact}"

    # A version the backend does not offer falls back to the closest older
    # profile (a slightly older Chrome is a smaller lie than a newer one);
    # below the oldest available profile it clamps to the oldest.
    older = _curl_impersonation_target(exact - 1)
    assert older.startswith("chrome")
    assert int(older[len("chrome"):]) <= exact - 1
    assert _curl_impersonation_target(1) == f"chrome{versions[0]}"
    assert _curl_impersonation_target(None) == f"chrome{exact}"


def test_the_impersonation_backend_decodes_the_body(server: str):
    """The optional backend decodes for itself; the transport must not
    decode a second time and corrupt the page."""
    transport = HttpTransport(tls_impersonation=True)
    if not transport.tls_impersonation:
        pytest.skip("the optional TLS backend is not installed")
    try:
        response = transport._open_with_curl(
            f"{server}/gzip",
            method="GET",
            body=None,
            headers=[("Accept-Encoding", "gzip"), ("User-Agent", "UA/1")],
            timeout=15.0,
        )
    finally:
        transport.close()
    assert b"compressed" in response.body


def test_transport_reuses_one_connection_per_origin(server: str):
    connections = []

    class _Counter(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            connections.append(id(self.connection))
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Counter)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        transport = HttpTransport()
        url = f"http://127.0.0.1:{httpd.server_address[1]}/x"
        transport.open(url)
        transport.open(url)
        transport.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert len(set(connections)) == 1


# --------------------------------------------------------------------- engine
def _fingerprint(isolate) -> Dict[str, Any]:
    """The values a fingerprinting script would collect."""
    raw = isolate.eval(
        r"""
(function () {
  var out = {};
  out.ua = navigator.userAgent;
  out.platform = navigator.platform;
  out.language = navigator.language;
  out.languages = navigator.languages.join(",");
  out.hardwareConcurrency = navigator.hardwareConcurrency;
  out.deviceMemory = navigator.deviceMemory;
  out.screen = [screen.width, screen.height, screen.availWidth, screen.availHeight].join("x");
  out.viewport = [innerWidth, innerHeight, outerWidth, outerHeight].join("x");
  out.brands = navigator.userAgentData.brands.map(function (b) {
    return b.brand + "/" + b.version;
  }).join(",");
  out.webdriver = String(navigator.webdriver);
  out.canvas = (function () {
    var c = document.createElement("canvas");
    var ctx = c.getContext("2d");
    if (!ctx) return "null";
    ctx.fillStyle = "#ff0000";
    ctx.fillRect(0, 0, 4, 4);
    return c.toDataURL().slice(0, 22) + "|" + c.toDataURL().slice(-16);
  })();
  out.geolocation = (function () {
    var state = "pending";
    navigator.geolocation.getCurrentPosition(
      function () { state = "granted"; },
      function (err) { state = "denied:" + err.code; }
    );
    return state;
  })();
  out.globals = Object.getOwnPropertyNames(globalThis).filter(function (k) {
    return k.indexOf("__iralens") >= 0;
  }).join(",");
  out.audio = (function () {
    try {
      var ctx = new AudioContext();
      return ctx.sampleRate + "/" + ctx.baseLatency;
    } catch (e) {
      return "unavailable";
    }
  })();
  return JSON.stringify(out);
})()
"""
    )
    return json.loads(raw)


def test_stealth_profile_reaches_the_page(server: str, tmp_path: Path):
    engine = ObscuraNativeEngine(
        allow_private=True, stealth=True, storage_dir=tmp_path / "storage"
    )
    try:
        engine.navigate(f"{server}/page", timeout=20)
        profile = engine.context.profile
        assert profile is not None
        seen = _fingerprint(engine.active_tab.isolate)
        assert seen["ua"] == profile.user_agent
        assert seen["platform"] == profile.platform
        assert seen["language"] == profile.language
        assert seen["languages"] == ",".join(profile.languages)
        assert seen["hardwareConcurrency"] == profile.hardware_concurrency
        assert seen["deviceMemory"] == profile.device_memory
        assert seen["screen"] == (
            f"{profile.screen_width}x{profile.screen_height}"
            f"x{profile.avail_width}x{profile.avail_height}"
        )
        assert seen["viewport"] == (
            f"{profile.inner_width}x{profile.inner_height}x"
            f"{profile.outer_width}x{profile.outer_height}"
        )
        assert seen["brands"] == ",".join(
            f"{b['brand']}/{b['version']}" for b in profile.brands
        )
        assert seen["webdriver"] == "false"
    finally:
        engine.close()


def test_the_identity_does_not_change_between_pages(server: str, tmp_path: Path):
    """The upstream engine re-seeds its fingerprint on every navigation.

    A browser does not change its screen size between two pages, and a
    client that does is flagged. Stealth pins it.
    """
    engine = ObscuraNativeEngine(
        allow_private=True, stealth=True, storage_dir=tmp_path / "storage"
    )
    try:
        engine.navigate(f"{server}/one", timeout=20)
        first = _fingerprint(engine.active_tab.isolate)
        engine.navigate(f"{server}/two", timeout=20)
        second = _fingerprint(engine.active_tab.isolate)
    finally:
        engine.close()
    for key in ("screen", "viewport", "hardwareConcurrency", "deviceMemory",
                "brands", "canvas", "audio"):
        assert first[key] == second[key], key


def test_the_canvas_is_readable_and_stable(server: str, tmp_path: Path):
    engine = ObscuraNativeEngine(
        allow_private=True, stealth=True, storage_dir=tmp_path / "storage"
    )
    try:
        engine.navigate(f"{server}/page", timeout=20)
        seen = _fingerprint(engine.active_tab.isolate)
    finally:
        engine.close()
    assert seen["canvas"] != "null"
    assert seen["canvas"].startswith("data:image/png;base64,")


def test_geolocation_is_denied_without_permission(server: str, tmp_path: Path):
    """A headless client that answers geolocation instantly is a bot tell."""
    engine = ObscuraNativeEngine(
        allow_private=True, stealth=True, storage_dir=tmp_path / "storage"
    )
    try:
        engine.navigate(f"{server}/page", timeout=20)
        seen = _fingerprint(engine.active_tab.isolate)
    finally:
        engine.close()
    assert seen["geolocation"] == "denied:1"


def test_the_stealth_layer_leaves_no_globals_behind(server: str, tmp_path: Path):
    engine = ObscuraNativeEngine(
        allow_private=True, stealth=True, storage_dir=tmp_path / "storage"
    )
    try:
        engine.navigate(f"{server}/page", timeout=20)
        seen = _fingerprint(engine.active_tab.isolate)
    finally:
        engine.close()
    assert seen["globals"] == ""


def test_without_stealth_the_page_is_untouched(server: str):
    """Stealth is opt-in: without it, the engine behaves as it always did."""
    engine = ObscuraNativeEngine(allow_private=True)
    try:
        engine.navigate(f"{server}/page", timeout=20)
        seen = _fingerprint(engine.active_tab.isolate)
    finally:
        engine.close()
    assert engine.context.profile is None
    assert seen["webdriver"] == "false"
    # No canvas context without the stealth layer's fallback.
    assert seen["canvas"] == "null"


# ------------------------------------------------------------------ challenges
def test_a_captcha_page_is_reported_not_returned(server: str):
    """A challenge page is not content."""
    engine = ObscuraNativeEngine(allow_private=True, stealth=True)
    try:
        with pytest.raises(VerificationRequiredError):
            engine.navigate(f"{server}/captcha", timeout=20)
    finally:
        engine.close()


def test_a_widget_on_a_readable_page_does_not_block_the_read(server: str):
    """Many sites put a reCAPTCHA badge on every article.

    Refusing to read those articles would be worse than the badge, so a
    widget only blocks when the page is essentially the widget.
    """
    engine = ObscuraNativeEngine(allow_private=True, stealth=True)
    try:
        outcome = engine.navigate(f"{server}/badge", timeout=20)
    finally:
        engine.close()
    assert outcome.ok
    assert outcome.title == "Article"


def test_a_solved_captcha_is_entered_into_the_page(server: str, monkeypatch):
    """The token from the solving service is entered the way a browser does."""
    seen: Dict[str, Any] = {}

    class _FakeSolver(captcha_mod.Solver):
        name = "fake"
        available = True

        def solve(self, info: captcha_mod.ChallengeInfo, *, timeout: float = 120.0) -> str:
            seen["sitekey"] = info.sitekey
            seen["provider"] = info.provider
            return "SOLVED-TOKEN"

    engine = ObscuraNativeEngine(allow_private=True, stealth=True)
    engine.context.solver = _FakeSolver()
    try:
        engine.navigate(f"{server}/captcha", timeout=20)
        value = engine.active_tab.isolate.eval(
            "document.querySelector('textarea[name=\"g-recaptcha-response\"]')"
            "?.value || ''"
        )
    finally:
        engine.close()
    assert seen["sitekey"] == "abcdefghij1234567890"
    assert seen["provider"] == "recaptcha"
    assert value == "SOLVED-TOKEN"


def test_sitekey_extraction():
    html = '<div class="g-recaptcha" data-sitekey="site-key-123456"></div>'
    assert captcha_mod.extract_sitekey(html) == "site-key-123456"
    assert captcha_mod.extract_sitekey("<div>nothing here</div>") == ""


def test_challenge_detection_names_the_provider():
    info = captcha_mod.detect(
        '<div class="g-recaptcha" data-sitekey="abcdefghij1234567890"></div>',
        url="https://example.com/",
    )
    assert info is not None
    assert info.kind == captcha_mod.KIND_CAPTCHA
    assert info.provider == "recaptcha"
    assert info.sitekey == "abcdefghij1234567890"


def test_a_page_without_a_challenge_is_not_flagged():
    assert captcha_mod.detect("<html><body>An ordinary article.</body></html>") is None


def test_no_solver_means_no_solving():
    solver = captcha_mod.build_solver("2captcha", None)
    assert not solver.available
    with pytest.raises(captcha_mod.SolverError):
        solver.solve(captcha_mod.ChallengeInfo(kind=captcha_mod.KIND_CAPTCHA))


def test_an_unknown_provider_is_not_a_solver():
    assert not captcha_mod.build_solver("not-a-provider", "key").available
    assert not captcha_mod.build_solver(None, "key").available
    assert not captcha_mod.build_solver("2captcha", "").available


def test_a_configured_provider_builds_a_solver():
    solver = captcha_mod.build_solver("2captcha", "key")
    assert solver.available
    assert solver.name == "2captcha"
    assert captcha_mod.build_solver("anticaptcha", "key").name == "anticaptcha"


def test_the_token_script_carries_the_token():
    info = captcha_mod.ChallengeInfo(
        kind=captcha_mod.KIND_CAPTCHA, provider="recaptcha", callback="onDone"
    )
    script = captcha_mod.token_injection_script("TOKEN-123", info)
    assert '"TOKEN-123"' in script
    assert "g-recaptcha-response" in script
    assert '"onDone"' in script


# ---------------------------------------------------------------- capabilities
def _handshake(extra_args: List[str], env: Dict[str, str] | None = None):
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-01", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1"}},
    }) + "\n"
    argv = [sys.executable, "-m", "iralens._internal.browser_engine.mcp_server",
            "mcp"] + list(extra_args)
    import os

    merged = dict(os.environ)
    merged.update(env or {})
    proc = subprocess.run(argv, input=request, capture_output=True, text=True,
                          timeout=180, env=merged)
    return proc.stdout, proc.stderr


def test_stealth_is_advertised_as_supported():
    assert mcp_server.ENGINE_CAPABILITIES["stealth"]["supported"] is True


def test_requested_stealth_is_applied_and_reported():
    out, err = _handshake(["--stealth"])
    stealth = json.loads(out.strip().splitlines()[0])["result"]["engineCapabilities"]["stealth"]
    assert stealth["requested"] is True
    assert stealth["applied"] is True
    assert stealth["supported"] is True
    assert "stealth" in err.lower()


def test_stealth_that_was_not_asked_for_is_off():
    out, err = _handshake([])
    stealth = json.loads(out.strip().splitlines()[0])["result"]["engineCapabilities"]["stealth"]
    assert stealth["requested"] is False
    assert stealth["applied"] is False
    assert "stealth" not in err.lower()


def test_the_captcha_solver_capability_follows_the_configuration():
    out, _ = _handshake(
        ["--stealth"],
        env={
            mcp_server.CAPTCHA_PROVIDER_ENV_VAR: "2captcha",
            mcp_server.CAPTCHA_KEY_ENV_VAR: "operator-key",
        },
    )
    solver = json.loads(out.strip().splitlines()[0])["result"]["engineCapabilities"]["captcha_solver"]
    assert solver["supported"] is True
    assert solver["provider"] == "2captcha"


def test_without_a_key_no_solver_is_advertised():
    out, _ = _handshake(["--stealth"])
    solver = json.loads(out.strip().splitlines()[0])["result"]["engineCapabilities"]["captcha_solver"]
    assert solver["supported"] is False
    assert solver["provider"] == ""


def test_the_tls_capability_is_reported_honestly():
    out, _ = _handshake(["--stealth"])
    tls = json.loads(out.strip().splitlines()[0])["result"]["engineCapabilities"]["tls_impersonation"]
    assert tls["supported"] is mcp_server.ENGINE_CAPABILITIES["tls_impersonation"]["supported"]
    assert "reason" in tls
