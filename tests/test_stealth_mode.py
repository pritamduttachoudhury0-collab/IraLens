# -*- coding: utf-8 -*-
"""Stealth mode: the identity is applied, coherent, and honestly reported.

The feature exists because IraLens used to accept `stealth: true` and change
nothing. These tests are the replacement contract, measured the way the
original audit measured it:

* on the wire, from a raw socket, so header *order* and *casing* are asserted
  rather than inferred from a parsed request;
* in the page, through the engine's own navigator hooks;
* at the boundary, where the capability report has to say what stealth does
  not do.

Every test here is local and offline.
"""

from __future__ import annotations

import gzip
import socket
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from iralens._internal.browser_engine.runtime import ObscuraNativeEngine
from iralens._internal.browser_engine.stealth import (
    DEFAULT_STEALTH_USER_AGENT,
    STEALTH_LIMITATIONS,
    ContentEncodingError,
    StealthProfile,
    build_stealth_capability,
    decode_body,
    profile_for_engine,
    profile_from_config,
    profile_from_user_agent,
    stealth_enabled,
)

PAGE = (
    "<!doctype html><html><head><title>Probe</title>"
    '<script src="/probe.js"></script></head>'
    "<body><p id=\"x\">probe</p></body></html>"
)

#: One recorded request: the request line, then headers in received order.
Recorded = Tuple[str, List[Tuple[str, str]]]


class _RawFixture:
    """A raw HTTP/1.1 server that records requests verbatim.

    `http.server` would normalise the header block; this keeps the bytes, which
    is the whole point of the stealth assertions.
    """

    def __init__(self, routes: Dict[str, Tuple[int, Dict[str, str], bytes]]) -> None:
        self.routes = routes
        self.requests: List[Recorded] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
            head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
            lines = head.split("\r\n")
            headers: List[Tuple[str, str]] = []
            for line in lines[1:]:
                if ": " in line:
                    name, value = line.split(": ", 1)
                    headers.append((name, value))
            self.requests.append((lines[0], headers))
            path = lines[0].split(" ")[1]
            status, extra, body = self.routes.get(
                path, (404, {"Content-Type": "text/plain"}, b"not found")
            )
            reason = {200: "OK", 404: "Not Found"}.get(status, "OK")
            out = [f"HTTP/1.1 {status} {reason}"]
            for name, value in extra.items():
                out.append(f"{name}: {value}")
            out.append(f"Content-Length: {len(body)}")
            out.append("Connection: close")
            conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + body)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


def _profile(**overrides: Any) -> StealthProfile:
    values: Dict[str, Any] = {
        "hardware_concurrency": 4,
        "device_memory": 8,
        "language": "en-US",
        "languages": ("en-US", "en"),
    }
    values.update(overrides)
    return profile_from_user_agent(DEFAULT_STEALTH_USER_AGENT, **values)


def _engine(profile: Optional[StealthProfile]) -> ObscuraNativeEngine:
    return ObscuraNativeEngine(
        stealth=profile is not None,
        stealth_profile=profile,
        allow_private=True,
    )


def _route(content_type: str, body: bytes, status: int = 200) -> Tuple[int, Dict[str, str], bytes]:
    return (status, {"Content-Type": content_type}, body)


@pytest.fixture()
def server() -> _RawFixture:
    fixture = _RawFixture({
        "/": _route("text/html; charset=utf-8", PAGE.encode("utf-8")),
        "/probe.js": _route("application/javascript", b"window.__probe = 1;"),
    })
    try:
        yield fixture
    finally:
        fixture.close()


def _names(headers: List[Tuple[str, str]]) -> List[str]:
    return [name for name, _ in headers]


def _value(headers: List[Tuple[str, str]], wanted: str) -> Optional[str]:
    for name, value in headers:
        if name.lower() == wanted.lower():
            return value
    return None


# --------------------------------------------------------------------- wire
def test_navigation_headers_match_the_chromium_wire_shape(server: _RawFixture) -> None:
    """Order and casing, not just presence: both were measured in the audit."""
    engine = _engine(_profile())
    try:
        engine.navigate(server.base_url + "/", timeout=15)
    finally:
        engine.close()

    request_line, headers = server.requests[0]
    assert request_line == "GET / HTTP/1.1"
    assert _names(headers) == [
        "Host",
        "Connection",
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
    ]
    assert _value(headers, "sec-ch-ua") == _profile().sec_ch_ua()
    assert _value(headers, "sec-ch-ua-mobile") == "?0"
    assert _value(headers, "sec-ch-ua-platform") == '"Windows"'
    assert _value(headers, "Upgrade-Insecure-Requests") == "1"
    assert _value(headers, "User-Agent") == DEFAULT_STEALTH_USER_AGENT
    assert _value(headers, "Sec-Fetch-Site") == "none"
    assert _value(headers, "Sec-Fetch-Mode") == "navigate"
    assert _value(headers, "Sec-Fetch-User") == "?1"
    assert _value(headers, "Sec-Fetch-Dest") == "document"
    assert _value(headers, "Accept-Language") == "en-US,en;q=0.9"


def test_script_subresource_is_requested_like_a_script(server: _RawFixture) -> None:
    """The audit's worst finding: classic scripts used a non-browser client."""
    engine = _engine(_profile())
    try:
        engine.navigate(server.base_url + "/", timeout=15)
    finally:
        engine.close()

    script = next(req for req in server.requests if req[0].startswith("GET /probe.js"))
    _, headers = script
    assert _value(headers, "User-Agent") == DEFAULT_STEALTH_USER_AGENT
    assert _value(headers, "Sec-Fetch-Site") == "same-origin"
    assert _value(headers, "Sec-Fetch-Mode") == "no-cors"
    assert _value(headers, "Sec-Fetch-Dest") == "script"
    assert _value(headers, "Sec-Fetch-User") is None
    assert _value(headers, "Referer") == server.base_url + "/"
    assert _value(headers, "Accept") == "*/*"
    assert _value(headers, "Upgrade-Insecure-Requests") is None
    assert _value(headers, "sec-ch-ua") == _profile().sec_ch_ua()


def test_stealth_off_keeps_the_previous_request_shape(server: _RawFixture) -> None:
    """Stealth is an explicit choice: it must not leak into the default path."""
    engine = _engine(None)
    try:
        engine.navigate(server.base_url + "/", timeout=15)
    finally:
        engine.close()
    _, headers = server.requests[0]
    lowered = [name.lower() for name in _names(headers)]
    assert "sec-fetch-site" not in lowered
    assert "sec-fetch-dest" not in lowered
    assert "sec-ch-ua" not in lowered
    assert _value(headers, "User-Agent") == DEFAULT_STEALTH_USER_AGENT


# ----------------------------------------------------------------------- JS
def _evaluate(engine: ObscuraNativeEngine, expression: str) -> Any:
    return engine.dispatch_tool("browser_evaluate", {"expression": expression})


def test_navigator_identity_matches_the_profile(server: _RawFixture) -> None:
    profile = _profile(language="de-DE", languages=("de-DE", "de"), screen_width=1600, screen_height=900)
    engine = _engine(profile)
    try:
        engine.navigate(server.base_url + "/", timeout=15)
        assert _evaluate(engine, "navigator.userAgent") == DEFAULT_STEALTH_USER_AGENT
        assert _evaluate(engine, "navigator.platform") == "Win32"
        assert _evaluate(engine, "navigator.languages.join(',')") == "de-DE,de"
        assert _evaluate(engine, "navigator.language") == "de-DE"
        assert _evaluate(engine, "navigator.userAgentData.platform") == "Windows"
        assert _evaluate(engine, "String(navigator.webdriver)") == "false"
        assert _evaluate(engine, "[screen.width,screen.height].join('x')") == "1600x900"
        assert _evaluate(engine, "String(globalThis.__obscura_stealth)") == "true"
    finally:
        engine.close()


def test_client_hints_and_user_agent_data_agree(server: _RawFixture) -> None:
    """A header and a JavaScript value describing different brands is a tell."""
    profile = _profile()
    engine = _engine(profile)
    try:
        engine.navigate(server.base_url + "/", timeout=15)
        brands = _evaluate(
            engine,
            "navigator.userAgentData.brands.map(b => b.brand + '=' + b.version).join('|')",
        )
    finally:
        engine.close()

    _, headers = server.requests[0]
    header = _value(headers, "sec-ch-ua") or ""
    from_header = [part.replace('"', "").replace(";v=", "=") for part in header.split(", ")]
    assert "|".join(from_header) == brands


#: Deliberately the first value of the engine's own stealth pool: if the pin
#: were not applied, four reloads would almost never all report it.
STABLE_CORES = 4


def test_hardware_identity_survives_navigation(server: _RawFixture) -> None:
    """`__obscura_init` reseeds its CPU pool each load; a real browser does not."""
    engine = _engine(_profile(hardware_concurrency=STABLE_CORES))
    seen = set()
    try:
        for _ in range(4):
            engine.navigate(server.base_url + "/", timeout=15)
            seen.add(_evaluate(engine, "String(navigator.hardwareConcurrency)"))
            assert _evaluate(engine, "String(globalThis.__obscura_hw)") == str(STABLE_CORES)
    finally:
        engine.close()
    assert seen == {str(STABLE_CORES)}


WEBGL_PROBE = "String(!!document.createElement('canvas').getContext('webgl'))"


def test_stealth_does_not_fabricate_webgl(server: _RawFixture) -> None:
    """Absence is honest; inventing a GL context would be a new fingerprint."""
    plain = _engine(None)
    stealth = _engine(_profile())
    try:
        plain.navigate(server.base_url + "/", timeout=15)
        without = _evaluate(plain, WEBGL_PROBE)
        stealth.navigate(server.base_url + "/", timeout=15)
        with_stealth = _evaluate(stealth, WEBGL_PROBE)
    finally:
        plain.close()
        stealth.close()
    assert with_stealth == without
    assert any("WebGL" in item for item in STEALTH_LIMITATIONS)


# ------------------------------------------------------------------ profiles
def test_custom_user_agent_moves_every_layer() -> None:
    profile = profile_from_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    )
    assert profile.ua_platform == "Linux"
    assert profile.navigator_platform == "Linux x86_64"
    assert profile.mobile is False
    headers = profile.build_headers(url="https://example.com/")
    assert headers["User-Agent"].endswith("Chrome/150.0.0.0 Safari/537.36")
    assert headers["sec-ch-ua-platform"] == '"Linux"'
    assert '"Chromium";v="150"' in headers["sec-ch-ua"]


def test_profile_from_config_reads_the_stealth_keys() -> None:
    class _Cfg(dict):
        pass

    cfg = _Cfg(
        stealth="true",
        stealth_language="fr-FR",
        stealth_screen="1366x768",
        stealth_geolocation="48.8566,2.3522",
    )
    profile = profile_from_config(cfg)
    assert profile is not None
    assert profile.language == "fr-FR"
    assert profile.languages == ("fr-FR", "fr")
    assert (profile.screen_width, profile.screen_height) == (1366, 768)
    assert profile.geo == (48.8566, 2.3522)
    assert profile.accept_language() == "fr-FR,fr;q=0.9"
    globals_before_init = profile.identity_globals()
    assert globals_before_init["__obscura_geo_lat"] == 48.8566
    assert globals_before_init["__obscura_language"] == "fr-FR"


def test_invalid_stealth_values_fall_back_instead_of_breaking() -> None:
    class _Cfg(dict):
        pass

    cfg = _Cfg(stealth="true", stealth_screen="huge", stealth_geolocation="999,999")
    profile = profile_from_config(cfg)
    assert profile is not None
    assert (profile.screen_width, profile.screen_height) == (1920, 1080)
    assert profile.geo is None


def test_stealth_flag_parsing() -> None:
    class _Cfg(dict):
        pass

    for on in ("1", "true", "YES", True):
        assert stealth_enabled(_Cfg(stealth=on)) is True
    for off in ("", "0", "false", None):
        assert stealth_enabled(_Cfg(stealth=off)) is False
    assert profile_for_engine(stealth=False) is None
    assert profile_for_engine(stealth=True) is not None


def test_sec_fetch_site_classification() -> None:
    profile = _profile()
    same = profile.build_headers(
        url="https://shop.example.com/app.js",
        resource_type="script",
        referrer="https://shop.example.com/",
    )
    assert same["Sec-Fetch-Site"] == "same-origin"
    sibling = profile.build_headers(
        url="https://cdn.example.com/app.js",
        resource_type="script",
        referrer="https://shop.example.com/",
    )
    assert sibling["Sec-Fetch-Site"] == "same-site"
    assert sibling["Referer"] == "https://shop.example.com/"
    cross = profile.build_headers(
        url="https://tracker.example.net/x.js",
        resource_type="script",
        referrer="https://shop.example.com/",
    )
    assert cross["Sec-Fetch-Site"] == "cross-site"
    # Cross-origin referrers are trimmed to the origin by default.
    assert cross["Referer"] == "https://shop.example.com/"


def test_referer_is_withheld_on_a_downgrade() -> None:
    profile = _profile()
    headers = profile.build_headers(
        url="http://example.com/a.js",
        resource_type="script",
        referrer="https://example.com/",
    )
    assert "Referer" not in headers


def test_cors_resources_send_an_origin_and_empty_dest() -> None:
    profile = _profile()
    headers = profile.build_headers(
        url="https://api.example.com/v1/items",
        resource_type="fetch",
        referrer="https://www.example.com/page",
        method="POST",
        content_type="application/x-www-form-urlencoded",
    )
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Sec-Fetch-Dest"] == "empty"
    assert headers["Origin"] == "https://www.example.com"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_client_hints_follow_chromes_secure_context_rule() -> None:
    """Chrome only sends Sec-CH-UA to secure contexts; emulate exactly that."""
    profile = _profile()
    public_http = profile.build_headers(url="http://plain.example.com/")
    assert "sec-ch-ua" not in public_http
    assert "sec-ch-ua-platform" not in public_http
    assert public_http["User-Agent"] == profile.user_agent

    https = profile.build_headers(url="https://plain.example.com/")
    assert https["sec-ch-ua"] == profile.sec_ch_ua()

    # Loopback is a secure context, which is why the reference capture on
    # http://127.0.0.1:8767 carries the hints.
    for url in (
        "http://127.0.0.1:8767/",
        "http://localhost:8767/",
        "http://[::1]:8767/",
        "http://app.localhost/",
    ):
        assert profile.build_headers(url=url)["sec-ch-ua"] == profile.sec_ch_ua(), url


def test_engine_headers_never_contain_a_duplicate_host(server: _RawFixture) -> None:
    engine = _engine(_profile())
    try:
        engine.navigate(server.base_url + "/", timeout=15)
    finally:
        engine.close()
    _, headers = server.requests[0]
    assert _names(headers).count("Host") == 1


# ------------------------------------------------------------- content coding
def test_gzip_response_is_decoded() -> None:
    body = gzip.compress(b"<html><body>compressed</body></html>")
    assert decode_body(body, "gzip") == b"<html><body>compressed</body></html>"


def test_corrupt_compressed_body_is_a_decoding_error() -> None:
    """A truncated stream must not surface as a generic network failure."""
    with pytest.raises(ContentEncodingError):
        decode_body(b"not actually gzip", "gzip")
    with pytest.raises(ContentEncodingError):
        decode_body(b"\x1f\x8b\x08\x00truncated", "gzip")


def test_unknown_content_encoding_is_reported_not_ignored() -> None:
    with pytest.raises(ContentEncodingError):
        decode_body(b"\x00\x01", "magic")


def test_advertised_encodings_are_decodable() -> None:
    """We must never advertise an encoding we cannot read back."""
    profile = _profile()
    for encoding in profile.accept_encodings:
        assert encoding in ("gzip", "deflate", "br", "zstd")
    assert "gzip" in profile.accept_encodings


# -------------------------------------------------------------- capabilities
def test_capabilities_state_the_boundary_not_just_the_feature() -> None:
    payload = build_stealth_capability(_profile())
    assert payload["applied"] is True
    assert payload["profile"]["user_agent"] == DEFAULT_STEALTH_USER_AGENT
    joined = " ".join(payload["limitations"]).lower()
    assert "captcha" in joined
    assert "tls" in joined
    assert "webgl" in joined


def test_engine_without_stealth_reports_it_as_not_applied() -> None:
    engine = ObscuraNativeEngine(allow_private=True)
    try:
        payload = engine.stealth_capability()
    finally:
        engine.close()
    assert payload["applied"] is False
    assert "profile" not in payload


def test_engine_with_stealth_reports_the_active_profile() -> None:
    profile = _profile()
    engine = ObscuraNativeEngine(stealth=True, stealth_profile=profile, allow_private=True)
    try:
        payload = engine.stealth_capability()
    finally:
        engine.close()
    assert payload["applied"] is True
    assert payload["profile"]["sec_ch_ua"] == profile.sec_ch_ua()
    assert payload["tier"] == "python-native-v8"


def test_configured_stealth_reaches_the_real_engine(
    tmp_path: Path, server: _RawFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config -> driver -> child process: the flag and its knobs must travel."""
    from iralens import IraLens
    from iralens.config import Config

    monkeypatch.setenv("IRALENS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("IRALENS_ALLOW_PRIVATE_NETWORK", "1")
    cfg = Config(tmp_path / "config.yaml")
    cfg._data.update({
        "stealth": "true",
        "stealth_language": "fr-FR",
        "stealth_screen": "1440x900",
        "allow_private": True,
    })
    with IraLens(cfg) as hil:
        hil.open(server.base_url + "/", mode="browser")
        assert hil.evaluate_js("navigator.language") == "fr-FR"
        assert hil.evaluate_js("[screen.width,screen.height].join('x')") == "1440x900"
        status = hil.engine().stealth_status()
    assert status["requested"] is True
    assert status["supported"] is True
    assert status["active"] is True
    assert status["profile"]["language"] == "fr-FR"

    _, headers = server.requests[0]
    assert _value(headers, "Accept-Language") == "fr-FR,fr;q=0.9"
    assert _value(headers, "Sec-Fetch-Dest") == "document"
    assert _value(headers, "sec-ch-ua-platform") == '"Windows"'


def test_child_argv_carries_every_configured_stealth_value() -> None:
    """The subprocess gets the same identity this process resolved.

    A flag without its knobs would make the child apply the default profile:
    headers and page would look like a different machine than configured, and
    the caller would never be told.
    """
    from iralens.engine.native import BrowserEngine

    engine = BrowserEngine(config={
        "stealth": "true",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/144.0.0.0 Safari/537.36",
        "stealth_user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/145.0.0.0 Safari/537.36",
        "stealth_language": "pt-BR",
        "stealth_screen": "1600x900",
        "stealth_geolocation": "-23.5,-46.6",
        "proxy": "http://127.0.0.1:3128",
        "storage_dir": "/tmp/iralens-store",
        "allow_private": "true",
    })
    argv, env = engine._argv_and_env(Path("/opt/iralens-browser-engine"))
    assert argv[1] == "mcp"
    assert "--stealth" in argv
    # stealth_user_agent wins over user_agent, exactly as in this process.
    assert argv[argv.index("--user-agent") + 1].endswith("Chrome/145.0.0.0 Safari/537.36")
    assert argv[argv.index("--language") + 1] == "pt-BR"
    assert argv[argv.index("--screen") + 1] == "1600x900"
    assert argv[argv.index("--geolocation") + 1] == "-23.5,-46.6"
    assert "--allow-private" in argv
    # The proxy must not be on argv (world-readable) but in the environment.
    assert "http://127.0.0.1:3128" not in " ".join(argv)
    assert "http://127.0.0.1:3128" in env.values()

    plain = BrowserEngine(config={"stealth": "false", "user_agent": "UA/1"})
    plain_argv, _ = plain._argv_and_env(Path("/opt/iralens-browser-engine"))
    assert "--stealth" not in plain_argv
    assert plain_argv[plain_argv.index("--user-agent") + 1] == "UA/1"


def test_stealth_off_in_config_leaves_the_engine_unmodified(
    tmp_path: Path, server: _RawFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from iralens import IraLens
    from iralens.config import Config

    monkeypatch.setenv("IRALENS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("IRALENS_ALLOW_PRIVATE_NETWORK", "1")
    cfg = Config(tmp_path / "config.yaml")
    cfg._data.update({"allow_private": True})
    with IraLens(cfg) as hil:
        hil.open(server.base_url + "/", mode="browser")
        status = hil.engine().stealth_status()
    assert status["requested"] is False
    assert status["active"] is False
    _, headers = server.requests[0]
    assert _value(headers, "Sec-Fetch-Site") is None


def test_doctor_reports_configured_stealth_before_the_engine_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` must not stay silent about a configured mode, nor claim it runs."""
    from iralens import IraLens
    from iralens.config import Config

    monkeypatch.setenv("IRALENS_HOME", str(tmp_path / "home"))
    cfg = Config(tmp_path / "config.yaml")
    cfg._data.update({"stealth": "true"})
    with IraLens(cfg) as hil:
        browser = hil.doctor()["browser"]
    assert browser["status"] == "ok"
    assert browser["stealth"]["requested"] is True
    assert browser["stealth"]["active"] is False
    assert "starts" in browser["stealth"]["note"]

    plain = Config(tmp_path / "plain.yaml")
    monkeypatch.setenv("IRALENS_HOME", str(tmp_path / "home2"))
    with IraLens(plain) as hil:
        assert "stealth" not in hil.doctor()["browser"]


def test_docs_describe_what_the_code_does() -> None:
    """The docs once said stealth had no effect; that claim must be gone."""
    root = Path(__file__).resolve().parent.parent
    for relative in ("README.md", "KNOWN_LIMITATIONS.md", "docs/security.md",
                     "docs/getting-started.md", "docs/stealth.md"):
        text = (root / relative).read_text(encoding="utf-8")
        assert "no effect in this distribution" not in text, relative
        assert "unreachable in every shipped configuration" not in text, relative
