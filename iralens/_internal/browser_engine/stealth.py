# -*- coding: utf-8 -*-
"""Stealth mode for the Python + native-V8 browser tier.

Stealth here means **one coherent browser identity applied to every layer this
tier controls**, not a bag of page-visible JavaScript overrides:

* the HTTP request headers (User-Agent, client hints, ``Sec-Fetch-*``,
  ``Accept``/``Accept-Language``/``Accept-Encoding``, ``Referer``, ``Origin``);
* the navigator surfaces the vendored engine already derives at page-init time
  (``navigator.userAgent``, ``userAgentData``, ``platform``, ``languages``,
  ``hardwareConcurrency``, ``deviceMemory``, ``screen``, geolocation).

The values come from a single :class:`StealthProfile`, so the header layer and
the JavaScript layer cannot drift apart. The engine knobs used to apply them
(``globalThis.__obscura_*``) are the same ones the upstream Rust tier sets in
``obscura-browser/src/page.rs``; nothing here patches ``Function.prototype`` or
fabricates a surface the host cannot provide.

What stealth is **not**:

* It does not impersonate Chrome's TLS/HTTP2 fingerprint. Requests leave
  through ``http.client`` and the host TLS stack, so the ClientHello is
  Python's, not Chrome's.
* It does not create WebGL, font differentiation, or Windows TCP/IP behaviour.
  Those surfaces stay absent or host-shaped, which is itself a signal.
* It does not solve CAPTCHAs or guarantee passage of a bot challenge.

``STEALTH_LIMITATIONS`` is surfaced through the engine capabilities payload so
callers see the boundary without reading this docstring.

Identity-consistency rules encoded here:

* ``sec-ch-ua`` brands use the *same* deterministic algorithm the vendored
  ``bootstrap.js`` uses for ``navigator.userAgentData.brands`` (Chromium's
  ``user_agent_utils.cc`` brand/GREASE permutation), so the header and the
  JavaScript value agree for any Chrome major version.
* A custom ``user_agent`` moves *all* layers: platform strings, client hints and
  ``navigator.userAgent`` are re-derived from it.
* ``hardwareConcurrency``/``deviceMemory`` are picked once per engine instance
  and held for every navigation, because a real browser does not change its CPU
  count on reload.
"""

from __future__ import annotations

import gzip
import json
import os
import random
import re
import zlib
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Default identity. Matches the engine's own fallback UA and the upstream
#: ``STEALTH_USER_AGENT`` constant, so a profile-less request and a stealth
#: request describe the same machine.
DEFAULT_STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

#: What this tier cannot make consistent. Reported verbatim by the engine so a
#: caller never mistakes stealth for an anti-bot guarantee.
STEALTH_LIMITATIONS: Tuple[str, ...] = (
    "TLS and HTTP/2 fingerprints are the host transport's, not Chrome's",
    "no WebGL/GPU surface is exposed (the engine has none), which detectors notice",
    "font metrics are engine-provided and do not vary by family",
    "TCP/IP stack characteristics remain the host kernel's",
    "browser-identity consistency only: CAPTCHAs and interactive challenges are not solved",
)

#: Resource classes the budgeted fetches can ask for, mapped to Chromium's
#: ``Sec-Fetch-Dest`` value (``empty`` for scripted fetch/XHR).
_DEST_BY_RESOURCE: Dict[str, str] = {
    "document": "document",
    "iframe": "iframe",
    "script": "script",
    "style": "style",
    "stylesheet": "style",
    "image": "image",
    "img": "image",
    "font": "font",
    "media": "video",
    "fetch": "empty",
    "xhr": "empty",
    "other": "empty",
    "empty": "empty",
}

#: Resource classes whose requests are CORS requests rather than ``no-cors``.
_CORS_RESOURCES = frozenset({"fetch", "xhr"})

_ACCEPT_HTML = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8,"
    "application/signed-exchange;v=b3;q=0.7"
)
_ACCEPT_IMAGE = (
    "image/jxl,image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
)

_ACCEPT_BY_RESOURCE: Dict[str, str] = {
    "document": _ACCEPT_HTML,
    "iframe": _ACCEPT_HTML,
    "style": "text/css,*/*;q=0.1",
    "stylesheet": "text/css,*/*;q=0.1",
    "image": _ACCEPT_IMAGE,
    "img": _ACCEPT_IMAGE,
    "media": "*/*",
    "font": "*/*",
    "script": "*/*",
    "fetch": "*/*",
    "xhr": "*/*",
    "other": "*/*",
    "empty": "*/*",
}

#: Chromium's GREASE brand machinery (components/embedder_support/
#: user_agent_utils.cc), mirrored from the vendored bootstrap.js so
#: ``sec-ch-ua`` and ``navigator.userAgentData.brands`` cannot disagree.
_GREASE_CHARS = [" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_"]
_GREASE_VER = ["8", "99", "24"]
_BRAND_PERMS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))

#: Multi-label public suffixes common enough to matter for ``Sec-Fetch-Site``.
#: Deliberately a short list, not a PSL: it only decides whether two hosts that
#: already failed the same-origin test are same-site.
_COMMON_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "or.jp", "ne.jp", "co.in",
    "net.in", "org.in", "com.au", "net.au", "org.au", "co.nz", "com.br",
    "com.cn", "com.hk", "com.sg", "com.mx", "co.za", "co.kr", "com.tr",
})

#: Header order mirroring Chromium's HTTP/1.1 captures as closely as this
#: transport allows. ``Host`` is emitted by ``http.client`` ahead of these.
_HEADER_ORDER: Tuple[str, ...] = (
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
    "Referer",
    "Origin",
    "Cookie",
    "Content-Type",
    "Accept-Encoding",
    "Accept-Language",
)

_UA_CHROME_RE = re.compile(r"Chrome/(\d+)")
_UA_WINDOWS_RE = re.compile(r"Windows NT ([\d.]+)")
_UA_MAC_RE = re.compile(r"Macintosh; Intel Mac OS X ([\d_]+)")
_UA_LINUX_RE = re.compile(r"Linux")

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class ContentEncodingError(RuntimeError):
    """The response used an encoding this build cannot decode."""


# --------------------------------------------------------------------------- io
def bool_from_config(value: Any) -> bool:
    """Parse the loose boolean spellings the config file accepts."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE_VALUES


def stealth_enabled(config: Any) -> bool:
    """True when the ``stealth`` config key asks for stealth mode."""
    if config is None:
        return False
    try:
        return bool_from_config(config.get("stealth", ""))
    except Exception:
        return False


def config_value(config: Any, key: str, default: str = "") -> str:
    """Read one config value as a stripped string (env overrides included)."""
    if config is None:
        return default
    try:
        raw = config.get(key, default)
    except Exception:
        return default
    if raw is None:
        return default
    return str(raw).strip()


def decodable_encodings() -> Tuple[str, ...]:
    """Content encodings this interpreter can decode, in Chrome's order.

    Only encodings we can actually turn back into bytes are advertised: sending
    ``br`` without a decoder would trade one tell (a short list) for a worse one
    (unreadable pages).
    """
    encodings: List[str] = ["gzip", "deflate"]
    if _has_brotli():
        encodings.append("br")
    try:
        import zstandard  # type: ignore[import-not-found]  # noqa: F401

        encodings.append("zstd")
    except Exception:
        pass
    return tuple(encodings)


def _brotli_module() -> Any:
    try:
        import brotli  # type: ignore[import-not-found]

        return brotli
    except Exception:
        pass
    try:
        import brotlicffi  # type: ignore[import-not-found]

        return brotlicffi
    except Exception:
        return None


def _has_brotli() -> bool:
    return _brotli_module() is not None


def decode_body(body: bytes, content_encoding: str) -> bytes:
    """Undo a response ``Content-Encoding``.

    Raises :class:`ContentEncodingError` for an encoding we did not advertise or
    cannot decode, so the caller reports it instead of returning decoded noise.
    """
    encoding = (content_encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return body
    # A server may stack encodings ("gzip, br"); apply them in reverse order.
    for part in reversed([p.strip() for p in encoding.split(",") if p.strip()]):
        try:
            if part in ("gzip", "x-gzip"):
                body = gzip.decompress(body)
            elif part == "deflate":
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    # Some servers send raw deflate without the zlib wrapper.
                    body = zlib.decompress(body, -zlib.MAX_WBITS)
            elif part == "br":
                module = _brotli_module()
                if module is None:
                    raise ContentEncodingError(
                        "brotli response but no brotli decoder is installed"
                    )
                body = module.decompress(body)
            elif part == "zstd":
                body = _zstd_decompress(body)
            else:
                raise ContentEncodingError(f"unsupported content encoding: {part}")
        except ContentEncodingError:
            raise
        except Exception as exc:
            # A truncated or corrupt stream is a decoding failure, not a
            # network one: report it as such instead of letting a zlib/OSError
            # surface as "network error".
            raise ContentEncodingError(
                f"{part} stream could not be decoded: {exc}"
            ) from exc
    return body


def _zstd_decompress(body: bytes) -> bytes:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        raise ContentEncodingError(
            "zstd response but no zstandard decoder is installed"
        ) from None
    return zstandard.ZstdDecompressor().decompress(body)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------- profile
def _grease_brand(major: int) -> Dict[str, str]:
    return {
        "brand": (
            "Not"
            + _GREASE_CHARS[major % 11]
            + "A"
            + _GREASE_CHARS[(major + 1) % 11]
            + "Brand"
        ),
        "version": _GREASE_VER[major % 3],
    }


def ua_brands(major: int) -> List[Dict[str, str]]:
    """Client-hint brands in the order Chromium emits them for ``major``."""
    ordered = [
        _grease_brand(major),
        {"brand": "Chromium", "version": str(major)},
        {"brand": "Google Chrome", "version": str(major)},
    ]
    perm = _BRAND_PERMS[major % 6]
    return [ordered[perm[0]], ordered[perm[1]], ordered[perm[2]]]


def _site_of(hostname: str) -> str:
    """Approximate eTLD+1 for ``Sec-Fetch-Site`` classification."""
    host = (hostname or "").strip(".").lower()
    if not host or _looks_like_ip(host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _COMMON_MULTI_LABEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _looks_like_ip(host: str) -> bool:
    if ":" in host:  # IPv6 literal
        return True
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def _split_url(url: str) -> Tuple[str, str, str]:
    """Return ``(scheme, hostname, origin)`` for an http(s) URL, else blanks."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url or "")
    except Exception:
        return "", "", ""
    scheme = (parts.scheme or "").lower()
    netloc = parts.netloc or ""
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not netloc:
        return scheme, host, ""
    return scheme, host, f"{scheme}://{netloc.lower()}"


def _is_potentially_trustworthy(scheme: str, host: str) -> bool:
    """Whether Chrome would treat the origin as a secure context.

    Client hints are gated on secure contexts. Chrome therefore sends
    ``Sec-CH-UA*`` to HTTPS origins and to loopback (``localhost``, ``127.0.0.1``,
    ``::1``, ``*.localhost``) -- which is why the reference capture over
    ``http://127.0.0.1:8767`` contains them -- but *not* to a plain-HTTP public
    origin. Sending them everywhere would be a fingerprint in the opposite
    direction: no stock Chrome emits ``Sec-CH-UA`` over ``http://``.
    """
    if scheme == "https":
        return True
    if scheme != "http":
        return False
    name = (host or "").strip("[]").lower()
    return (
        name in ("localhost", "127.0.0.1", "::1")
        or name.endswith(".localhost")
    )


def _referer_for(target_url: str, referrer: str) -> str:
    """Chrome's default referrer policy (``strict-origin-when-cross-origin``).

    Same-origin keeps the full path; a cross-origin request sends only the
    referring origin; an https-to-http downgrade sends nothing. Fragments are
    never sent.
    """
    target_scheme, _, target_origin = _split_url(target_url)
    ref_scheme, _, ref_origin = _split_url(referrer)
    if not target_origin or not ref_origin:
        return ""
    if ref_scheme == "https" and target_scheme == "http":
        return ""
    if target_origin == ref_origin:
        return referrer.split("#", 1)[0]
    return ref_origin + "/"


@dataclass(frozen=True)
class StealthProfile:
    """A single coherent desktop-Chrome identity."""

    user_agent: str = DEFAULT_STEALTH_USER_AGENT
    navigator_platform: str = "Win32"
    ua_platform: str = "Windows"
    ua_platform_version: str = "15.0.0"
    language: str = "en-US"
    languages: Tuple[str, ...] = ("en-US", "en")
    mobile: bool = False
    screen_width: int = 1920
    screen_height: int = 1080
    hardware_concurrency: int = field(default_factory=lambda: random.choice([4, 6, 8, 12, 16]))
    device_memory: int = field(default_factory=lambda: random.choice([4, 8]))
    #: ``(latitude, longitude)`` handed to the engine's geolocation shim. Unset
    #: by default: inventing a location is worse than leaving the engine's
    #: documented placeholder unless an operator chooses one.
    geo: Optional[Tuple[float, float]] = None
    accept_encodings: Tuple[str, ...] = field(default_factory=decodable_encodings)

    # ------------------------------------------------------------- derived
    @property
    def major(self) -> int:
        match = _UA_CHROME_RE.search(self.user_agent)
        return int(match.group(1)) if match else 0

    @property
    def full_version(self) -> str:
        match = re.search(r"Chrome/([\d.]+)", self.user_agent)
        return match.group(1) if match else ""

    def brands(self) -> List[Dict[str, str]]:
        return ua_brands(self.major or 145)

    def full_version_list(self) -> List[Dict[str, str]]:
        full = self.full_version or f"{self.major or 145}.0.0.0"
        return [{"brand": b["brand"], "version": full} for b in self.brands()]

    def sec_ch_ua(self) -> str:
        return ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in self.brands())

    def accept_language(self) -> str:
        """``Accept-Language`` matching ``navigator.languages``, Chrome-shaped."""
        langs = list(self.languages) or [self.language]
        out = [langs[0]]
        quality = 0.9
        for lang in langs[1:]:
            out.append(f"{lang};q={quality:.1f}")
            quality = round(max(0.1, quality - 0.1), 1)
        return ",".join(out)

    def accept_encoding(self) -> str:
        return ", ".join(self.accept_encodings or ("gzip", "deflate"))

    def user_agent_metadata(self) -> Dict[str, Any]:
        """The ``navigator.userAgentData`` shape this profile produces."""
        return {
            "brands": self.brands(),
            "fullVersionList": self.full_version_list(),
            "fullVersion": self.full_version or f"{self.major or 145}.0.0.0",
            "platform": self.ua_platform,
            "platformVersion": self.ua_platform_version,
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "mobile": self.mobile,
            "wow64": False,
        }

    # ---------------------------------------------------------------- JS tier
    def identity_globals(self) -> Dict[str, Any]:
        """Globals the vendored engine reads at ``__obscura_init`` time.

        These are the exact hooks upstream's Rust tier writes
        (``set_stealth`` / ``set_user_agent`` / ``set_platform`` /
        ``set_locale`` / ``set_geolocation`` / ``set_screen_size_override``).
        """
        values: Dict[str, Any] = {
            "__obscura_stealth": True,
            "__obscura_ua": self.user_agent,
            "__obscura_platform": self.navigator_platform,
            "__obscura_ua_platform": self.ua_platform,
            "__obscura_ua_platform_version": self.ua_platform_version,
            "__obscura_language": self.language,
            "__obscura_screen_w": int(self.screen_width),
            "__obscura_screen_h": int(self.screen_height),
        }
        if self.geo is not None:
            values["__obscura_geo_lat"] = float(self.geo[0])
            values["__obscura_geo_lon"] = float(self.geo[1])
        return values

    def post_init_globals(self) -> Dict[str, Any]:
        """Globals re-applied after ``__obscura_init``.

        ``__obscura_init`` reseeds the CPU/memory pools on every navigation, so
        a reload would otherwise change ``hardwareConcurrency``. A real browser
        never does that; pinning the same values for the whole engine instance
        removes the tell without patching the engine's getters.
        """
        return {
            "__obscura_hw": int(self.hardware_concurrency),
            "__obscura_mem": int(self.device_memory),
        }

    def js_preamble(self) -> str:
        return global_assignments(self.identity_globals())

    def describe(self) -> Dict[str, Any]:
        """Machine-readable identity for capability reporting."""
        return {
            "user_agent": self.user_agent,
            "platform": self.navigator_platform,
            "ua_platform": self.ua_platform,
            "ua_platform_version": self.ua_platform_version,
            "language": self.language,
            "languages": list(self.languages),
            "screen": [self.screen_width, self.screen_height],
            "hardware_concurrency": self.hardware_concurrency,
            "device_memory": self.device_memory,
            "sec_ch_ua": self.sec_ch_ua(),
            "accept_language": self.accept_language(),
            "accept_encoding": self.accept_encoding(),
            "geolocation": list(self.geo) if self.geo else None,
        }

    # ------------------------------------------------------------ HTTP tier
    def build_headers(
        self,
        *,
        url: str,
        resource_type: str = "document",
        referrer: Optional[str] = None,
        initiator: Optional[str] = None,
        method: str = "GET",
        cookie: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Dict[str, str]:
        """Request headers for one hop, in Chromium's wire order.

        ``Host`` is intentionally absent: ``http.client`` writes it first from
        the connection's own view of the request, which stays correct behind a
        proxy and avoids duplicating what the transport already knows.
        """
        resource = (resource_type or "document").strip().lower()
        if resource not in _DEST_BY_RESOURCE:
            resource = "other"
        dest = _DEST_BY_RESOURCE[resource]
        is_navigation = dest == "document"
        scheme, target_host, target_origin = _split_url(url)
        method_upper = str(method or "GET").upper()

        context_url = referrer or initiator or ""
        init_scheme, init_host, init_origin = _split_url(context_url)

        if is_navigation and not init_origin:
            fetch_site = "none"
        elif not init_origin:
            fetch_site = "same-origin" if target_origin else "none"
        elif target_origin == init_origin:
            fetch_site = "same-origin"
        elif target_origin and _site_of(target_host) == _site_of(init_host):
            fetch_site = "same-site"
        else:
            fetch_site = "cross-site"
        if init_scheme == "https" and scheme == "http" and fetch_site != "none":
            # https -> http is never same-origin/same-site for Sec-Fetch purposes.
            fetch_site = "cross-site"

        headers: Dict[str, str] = {"Connection": "keep-alive"}
        if _is_potentially_trustworthy(scheme, target_host):
            # Secure-context only, exactly as Chrome gates client hints.
            headers["sec-ch-ua"] = self.sec_ch_ua()
            headers["sec-ch-ua-mobile"] = "?1" if self.mobile else "?0"
            headers["sec-ch-ua-platform"] = f'"{self.ua_platform}"'
        if is_navigation and scheme in ("http", "https"):
            headers["Upgrade-Insecure-Requests"] = "1"
        headers["User-Agent"] = self.user_agent
        headers["Accept"] = _ACCEPT_BY_RESOURCE.get(resource, "*/*")
        headers["Sec-Fetch-Site"] = fetch_site
        headers["Sec-Fetch-Mode"] = (
            "navigate" if is_navigation else ("cors" if resource in _CORS_RESOURCES else "no-cors")
        )
        if is_navigation:
            headers["Sec-Fetch-User"] = "?1"
        headers["Sec-Fetch-Dest"] = dest

        referer = _referer_for(url, referrer) if referrer else ""
        if referer:
            headers["Referer"] = referer

        # Origin: every CORS request, cross-origin non-GET subresources, and
        # non-GET navigations (form posts). A same-origin GET sends none.
        origin = ""
        if is_navigation:
            if method_upper not in ("GET", "HEAD") and init_origin:
                origin = init_origin
        elif resource in _CORS_RESOURCES:
            candidate = init_origin or target_origin
            if candidate and (candidate != target_origin or method_upper not in ("GET", "HEAD")):
                origin = candidate
        elif init_origin and init_origin != target_origin and method_upper not in ("GET", "HEAD"):
            origin = init_origin
        if origin:
            headers["Origin"] = origin

        if cookie:
            headers["Cookie"] = cookie
        if content_type:
            headers["Content-Type"] = content_type
        headers["Accept-Encoding"] = self.accept_encoding()
        headers["Accept-Language"] = self.accept_language()

        ordered = {name: headers[name] for name in _HEADER_ORDER if name in headers}
        # Defensive: keep any future addition rather than dropping it silently.
        for name, value in headers.items():
            ordered.setdefault(name, value)
        return ordered


def global_assignments(values: Dict[str, Any]) -> str:
    """Render ``globalThis.__obscura_* = …`` assignments for the V8 isolate."""
    lines: List[str] = []
    for key, value in values.items():
        if not key.startswith("__obscura_"):
            raise ValueError(f"refusing to set non-engine global {key!r}")
        lines.append(f"globalThis[{json.dumps(key)}] = {json.dumps(value)};")
    return "\n".join(lines)


def profile_from_user_agent(user_agent: str, **overrides: Any) -> StealthProfile:
    """Derive platform strings from a UA so every layer describes one machine."""
    ua = (user_agent or "").strip() or DEFAULT_STEALTH_USER_AGENT
    windows = _UA_WINDOWS_RE.search(ua)
    mac = _UA_MAC_RE.search(ua)
    if windows:
        defaults: Dict[str, Any] = {
            "navigator_platform": "Win32",
            "ua_platform": "Windows",
            "ua_platform_version": "15.0.0",
        }
    elif mac:
        defaults = {
            "navigator_platform": "MacIntel",
            "ua_platform": "macOS",
            "ua_platform_version": "10.15.7",
        }
    elif _UA_LINUX_RE.search(ua) and "Android" not in ua:
        defaults = {
            "navigator_platform": "Linux x86_64",
            "ua_platform": "Linux",
            "ua_platform_version": "",
        }
    else:
        defaults = {}

    if "Android" in ua:
        defaults.update({"mobile": True, "navigator_platform": "Linux armv81"})
    if "iPhone" in ua or "iPad" in ua:
        defaults.update({"mobile": True, "navigator_platform": "iPhone"})

    merged: Dict[str, Any] = dict(defaults)
    merged.update({k: v for k, v in overrides.items() if v is not None})
    return StealthProfile(user_agent=ua, **merged)


def _parse_geo(raw: str) -> Optional[Tuple[float, float]]:
    if not raw:
        return None
    try:
        lat_text, lon_text = raw.split(",", 1)
        lat, lon = float(lat_text.strip()), float(lon_text.strip())
    except Exception:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def _parse_screen(raw: str) -> Optional[Tuple[int, int]]:
    if not raw:
        return None
    match = re.fullmatch(r"\s*(\d{3,5})\s*[xX*]\s*(\d{3,5})\s*", raw)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width < 320 or height < 240:
        return None
    return (width, height)


def language_from_environment() -> str:
    """Best-effort locale for the process, in BCP-47 form.

    The JavaScript tier cannot be made to report a locale that V8 itself does
    not resolve (that would mean patching ``Intl``), so the profile takes the
    host's language rather than inventing one the page can contradict.
    """
    for candidate in (
        os.environ.get("IRALENS_STEALTH_LANGUAGE", ""),
        os.environ.get("LC_ALL", ""),
        os.environ.get("LC_MESSAGES", ""),
        os.environ.get("LANG", ""),
        os.environ.get("LANGUAGE", ""),
    ):
        raw = (candidate or "").split(":")[0].split(".")[0].split("@")[0].strip()
        if not raw or raw in ("C", "POSIX"):
            continue
        raw = raw.replace("_", "-")
        if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", raw):
            return raw
    return "en-US"


def _languages_for(language: str) -> Tuple[str, ...]:
    base = language.split("-")[0]
    return (language, base) if base and base != language else (language,)


def profile_from_config(config: Any) -> Optional[StealthProfile]:
    """Build the profile for a config object, or ``None`` when stealth is off."""
    if not stealth_enabled(config):
        return None
    language = config_value(config, "stealth_language") or language_from_environment()
    return _profile_from_sources(
        config=config,
        user_agent=None,
        language=language,
        screen=config_value(config, "stealth_screen"),
        geolocation=config_value(config, "stealth_geolocation"),
    )


def profile_for_engine(
    *,
    stealth: bool = False,
    user_agent: Optional[str] = None,
    config: Any = None,
    language: Optional[str] = None,
    screen: Optional[str] = None,
    geolocation: Optional[str] = None,
) -> Optional[StealthProfile]:
    """Resolve a profile from engine flags, falling back to config values.

    Explicit CLI flags win over config; ``stealth`` may be turned on by either.
    """
    if not stealth:
        stealth = stealth_enabled(config)
    if not stealth:
        return None
    return _profile_from_sources(
        config=config,
        user_agent=user_agent,
        language=language or config_value(config, "stealth_language") or language_from_environment(),
        screen=screen or config_value(config, "stealth_screen"),
        geolocation=geolocation or config_value(config, "stealth_geolocation"),
    )


def _profile_from_sources(
    *,
    config: Any,
    user_agent: Optional[str],
    language: str,
    screen: str,
    geolocation: str,
) -> StealthProfile:
    ua = (
        user_agent
        or config_value(config, "stealth_user_agent")
        or config_value(config, "user_agent")
        or DEFAULT_STEALTH_USER_AGENT
    )
    overrides: Dict[str, Any] = {
        "language": language,
        "languages": _languages_for(language),
    }
    parsed_screen = _parse_screen(screen)
    if parsed_screen:
        overrides["screen_width"], overrides["screen_height"] = parsed_screen
    geo = _parse_geo(geolocation or os.environ.get("OBSCURA_GEOLOCATION", ""))
    if geo:
        overrides["geo"] = geo
    return replace(profile_from_user_agent(ua), **overrides)


def build_stealth_capability(
    profile: Optional[StealthProfile], *, error: Optional[str] = None
) -> Dict[str, Any]:
    """The ``engineCapabilities.stealth`` block for a real engine instance."""
    applied = profile is not None and not error
    payload: Dict[str, Any] = {
        "applied": applied,
        "tier": "python-native-v8",
        "layers": ["http_request_identity", "navigator_identity", "client_hints"],
        "limitations": list(STEALTH_LIMITATIONS),
    }
    if profile is not None:
        payload["profile"] = profile.describe()
    if error:
        payload["reason"] = error
    return payload


def header_pairs(headers: Dict[str, str]) -> Iterable[Tuple[str, str]]:
    """Stable ``(name, value)`` iteration for tests and debug output."""
    return tuple(headers.items())
