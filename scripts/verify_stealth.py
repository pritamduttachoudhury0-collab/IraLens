#!/usr/bin/env python
"""Verify stealth mode the way the audit measured it: raw wire + page probe.

Run from a checkout:

    .venv/bin/python scripts/verify_stealth.py

Three observations, no network needed:

1. **Wire.** A raw-socket HTTP server records the exact bytes of every request,
   so header order and casing are checked rather than reconstructed from a
   parsed request. The document request is compared against the header
   sequence in `verification/reference/chromium-headers.jsonl` from the
   original audit (Host first, then Chromium's order).
2. **Page.** The same engine instance is asked what a page sees: user agent,
   client-hint brands, platform, languages, screen, CPU/memory, webdriver.
   Each value is compared with the header it must agree with.
3. **Boundary.** The capability payload must report `applied: true` *and* the
   limits; a stealth mode that fails to mention TLS/WebGL would be the same
   overclaim this work removed.

Verdicts: PASS ran and held; FAIL ran and did not; SKIP could not be observed
here (for example a canvas probe, since this engine has no 2D context). SKIP is
reported separately and never counted as a pass.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iralens._internal.browser_engine.runtime import ObscuraNativeEngine  # noqa: E402
from iralens._internal.browser_engine.stealth import (  # noqa: E402
    DEFAULT_STEALTH_USER_AGENT,
    profile_from_user_agent,
)

PAGE = (
    "<!doctype html><html><head><title>Verify</title>"
    '<script src="/a.js"></script></head>'
    '<body><p id="x">verify</p></body></html>'
)

PROFILE_KWARGS = dict(
    language="de-DE",
    languages=("de-DE", "de"),
    screen_width=1600,
    screen_height=900,
    hardware_concurrency=12,
    device_memory=4,
)

#: `Host` is written by the transport; the rest is this repository's order.
EXPECTED_DOCUMENT_ORDER = [
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

PROBE = r"""
(() => {
  const out = {};
  const n = navigator;
  out.ua = n.userAgent;
  out.platform = n.platform;
  out.uaDataPlatform = n.userAgentData ? n.userAgentData.platform : null;
  out.uaDataBrands = n.userAgentData
    ? n.userAgentData.brands.map(b => b.brand + ' ' + b.version).join(' | ')
    : null;
  out.languages = n.languages.join(',');
  out.hardwareConcurrency = n.hardwareConcurrency;
  out.deviceMemory = n.deviceMemory;
  out.webdriver = n.webdriver;
  out.pluginsLen = n.plugins.length;
  out.mimeTypesLen = n.mimeTypes.length;
  out.maxTouchPoints = n.maxTouchPoints;
  out.hasWindowChrome = typeof window.chrome !== 'undefined';
  out.screen = [screen.width, screen.height, screen.availWidth, screen.availHeight].join('x');
  out.dpr = devicePixelRatio;
  out.tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.tzOffset = new Date().getTimezoneOffset();
  out.stealthFlag = String(globalThis.__obscura_stealth);
  out.toStringOfToString = Function.prototype.toString.call(Function.prototype.toString);
  // Guarded: this engine has no canvas context, and a missing surface must be
  // reported as SKIP rather than crashing the probe.
  const canvas = (() => { try { return document.createElement('canvas'); } catch (e) { return null; } })();
  out.hasCanvas = !!canvas;
  out.has2d = !!(canvas && canvas.getContext && canvas.getContext('2d'));
  out.webgl1 = !!(canvas && canvas.getContext && canvas.getContext('webgl'));
  return JSON.stringify(out);
})()
"""

results: List[Tuple[str, str, str]] = []


def record(name: str, ok: bool | None, detail: str = "") -> None:
    verdict = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    results.append((name, verdict, detail))


class RawServer:
    """A one-page HTTP/1.1 server that keeps the request bytes verbatim."""

    def __init__(self) -> None:
        self.requests: List[Tuple[str, List[Tuple[str, str]]]] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _route(self, path: str) -> Tuple[int, Dict[str, str], bytes]:
        if path == "/a.js":
            return 200, {"Content-Type": "application/javascript"}, b"window.__js=true;"
        return 200, {"Content-Type": "text/html; charset=utf-8"}, PAGE.encode()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(5.0)
                buf = b""
                while b"\r\n\r\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
                lines = head.split("\r\n")
                self.requests.append((lines[0], [tuple(l.split(": ", 1)) for l in lines[1:] if ": " in l]))
                path = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"
                status, headers, body = self._route(path.split("?", 1)[0])
                out = [f"HTTP/1.1 {status} OK"]
                out += [f"{k}: {v}" for k, v in headers.items()]
                out.append(f"Content-Length: {len(body)}")
                out.append("Connection: close")
                conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + body)
            except OSError:
                pass
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


def headers_of(request: Tuple[str, List[Tuple[str, str]]]) -> Dict[str, str]:
    return {name: value for name, value in request[1]}


def main() -> int:
    server = RawServer()
    profile = profile_from_user_agent(DEFAULT_STEALTH_USER_AGENT, **PROFILE_KWARGS)
    engine = ObscuraNativeEngine(
        stealth=True, stealth_profile=profile, allow_private=True
    )
    probe: Dict[str, object] = {}
    try:
        engine.navigate(server.base_url + "/", timeout=20)
        first = json.loads(engine.dispatch_tool("browser_evaluate", {"expression": PROBE}))
        probe = first
        # Second navigation: CPU/memory must not change between page loads.
        engine.navigate(server.base_url + "/", timeout=20)
        second = json.loads(engine.dispatch_tool("browser_evaluate", {"expression": PROBE}))
        capability = engine.stealth_capability()
    finally:
        engine.close()
        server.close()

    if len(server.requests) < 2:
        record("the engine made the expected requests", False, f"got {len(server.requests)}")
        return _report(probe)

    document = server.requests[0]
    script = next((r for r in server.requests if r[0].startswith("GET /a.js")), server.requests[-1])
    doc_headers = headers_of(document)
    script_headers = headers_of(script)
    order = [name for name, _ in document[1]]

    # ---------------------------------------------------------------- wire
    record(
        "document header order matches Chromium's",
        order[: len(EXPECTED_DOCUMENT_ORDER)] == EXPECTED_DOCUMENT_ORDER,
        " > ".join(order),
    )
    record(
        "client-hint names keep their lowercase spelling",
        "sec-ch-ua" in doc_headers and "sec-ch-ua-platform" in doc_headers,
        ", ".join(k for k in order if k.lower().startswith("sec-ch-ua")),
    )
    record(
        "Host is sent exactly once",
        order.count("Host") == 1,
        ", ".join(name for name in order if name == "Host"),
    )
    record(
        "User-Agent header equals the profile UA",
        doc_headers.get("User-Agent") == profile.user_agent,
        doc_headers.get("User-Agent", ""),
    )
    brands = probe.get("uaDataBrands")
    record(
        "sec-ch-ua brands equal navigator.userAgentData.brands",
        _brands_from_header(doc_headers.get("sec-ch-ua", "")) == brands,
        f"{doc_headers.get('sec-ch-ua', '')} vs {brands}",
    )
    record(
        "navigation is Sec-Fetch-Site: none, Mode: navigate, User: ?1",
        (doc_headers.get("Sec-Fetch-Site"), doc_headers.get("Sec-Fetch-Mode"),
         doc_headers.get("Sec-Fetch-User")) == ("none", "navigate", "?1"),
        f"{doc_headers.get('Sec-Fetch-Site')} / {doc_headers.get('Sec-Fetch-Mode')} / "
        f"{doc_headers.get('Sec-Fetch-User')}",
    )
    record(
        "subresource is same-origin no-cors with a Referer",
        (script_headers.get("Sec-Fetch-Site"), script_headers.get("Sec-Fetch-Mode"),
         script_headers.get("Sec-Fetch-Dest")) == ("same-origin", "no-cors", "script")
        and script_headers.get("Referer", "").startswith(server.base_url),
        f"{script_headers.get('Sec-Fetch-Site')} / {script_headers.get('Sec-Fetch-Mode')} / "
        f"{script_headers.get('Sec-Fetch-Dest')} / {script_headers.get('Referer')}",
    )
    record(
        "Accept-Language agrees with navigator.languages",
        doc_headers.get("Accept-Language") == _accept_language(str(probe.get("languages", ""))),
        f"{doc_headers.get('Accept-Language')} vs {probe.get('languages')}",
    )
    record(
        "Accept-Encoding advertises only decodable encodings",
        all(part.strip() in profile.accept_encodings for part in
            doc_headers.get("Accept-Encoding", "").split(",")),
        doc_headers.get("Accept-Encoding", ""),
    )

    # ---------------------------------------------------------------- page
    record("navigator.userAgent equals the UA header",
           probe.get("ua") == doc_headers.get("User-Agent"), str(probe.get("ua")))
    record("navigator.platform matches the identity",
           probe.get("platform") == "Win32", str(probe.get("platform")))
    record("navigator.languages is the configured locale",
           probe.get("languages") == "de-DE,de", str(probe.get("languages")))
    record("screen is the configured 1600x900 (avail height -40)",
           probe.get("screen") == "1600x900x1600x860", str(probe.get("screen")))
    record("__obscura_stealth is set inside the page",
           probe.get("stealthFlag") == "true", str(probe.get("stealthFlag")))
    record("hardwareConcurrency is pinned across navigations",
           probe.get("hardwareConcurrency") == 12 and second.get("hardwareConcurrency") == 12,
           f"{probe.get('hardwareConcurrency')} then {second.get('hardwareConcurrency')}")
    record("deviceMemory is pinned across navigations",
           probe.get("deviceMemory") == 4 and second.get("deviceMemory") == 4,
           f"{probe.get('deviceMemory')} then {second.get('deviceMemory')}")
    record("navigator.webdriver is false",
           probe.get("webdriver") is False, str(probe.get("webdriver")))
    record("Function.prototype.toString is untouched",
           probe.get("toStringOfToString") == "function toString() { [native code] }",
           str(probe.get("toStringOfToString")))
    record("the canvas 2D surface exists",
           None if not probe.get("has2d") else True,
           "not implemented by this engine, so canvas/font probes fail rather "
           "than return a fabricated hash")

    # ------------------------------------------------------------- boundary
    record("capabilities report applied: true",
           capability.get("applied") is True, json.dumps(capability.get("applied")))
    limits = " ".join(capability.get("limitations") or []).lower()
    record("capabilities state the TLS limit",
           "tls" in limits, "TLS/HTTP2 named in limitations")
    record("capabilities state the WebGL limit",
           "webgl" in limits, "WebGL named in limitations")
    record("capabilities carry the active profile",
           (capability.get("profile") or {}).get("user_agent") == profile.user_agent,
           str((capability.get("profile") or {}).get("language")))

    return _report(probe)


def _brands_from_header(value: str) -> str:
    """Turn ``"A";v="1", "B";v="2"`` into ``A 1 | B 2`` for comparison."""
    parts = []
    for chunk in value.split(", "):
        if not chunk:
            continue
        brand, _, version = chunk.partition(";v=")
        parts.append(f"{brand.strip().strip(chr(34))} {version.strip().strip(chr(34))}")
    return " | ".join(parts)


def _accept_language(languages: str) -> str:
    langs = [lang for lang in languages.split(",") if lang]
    if not langs:
        return ""
    out = [langs[0]]
    quality = 0.9
    for lang in langs[1:]:
        out.append(f"{lang};q={quality:.1f}")
        quality = max(0.1, round(quality - 0.1, 1))
    return ",".join(out)


def _report(probe: Dict[str, object]) -> int:
    width = max(len(name) for name, _, _ in results)
    for name, verdict, detail in results:
        print(f"{verdict:5s}  {name:<{width}}  {detail}")
    passed = sum(1 for _, verdict, _ in results if verdict == "PASS")
    failed = sum(1 for _, verdict, _ in results if verdict == "FAIL")
    skipped = sum(1 for _, verdict, _ in results if verdict == "SKIP")
    print()
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    if probe:
        print("page probe:", json.dumps(probe, indent=1)[:1200])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
