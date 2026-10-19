# -*- coding: utf-8 -*-
"""Stealth: one coherent browser identity for the whole session.

What "stealth" means here
-------------------------
Anti-bot systems do not look for one magic property. They compare the
*request* (header set, header order, header casing, connection reuse, TLS
handshake) with the *document* (what the page's JavaScript can observe) and
with what that same client did a minute ago. A client that claims to be
Chrome 145 on Windows but sends ``Connection: close``, no ``sec-ch-ua`` and a
``deviceMemory`` of 2 is not "stealthy" -- it is *inconsistent*, and
inconsistency is what gets flagged.

So this module owns a single :class:`StealthProfile`: a complete, internally
consistent description of one imaginary desktop Chrome, derived from a seed
and kept stable for the lifetime of the engine's storage directory. Every
layer reads from that one object:

* the HTTP layer (:mod:`.transport`) sends the header set Chrome would send,
  in the order Chrome sends it;
* the page layer (:mod:`stealth.js`) reports the same screen, GPU, core
  count and memory the headers imply;
* the brand list in ``Sec-CH-UA`` is the brand list ``navigator.userAgentData``
  reports, in the same order, because both are derived from the same field.

What this is not
----------------
It is not a CAPTCHA solver and it is not a TLS impersonator. It does not
forge challenge tokens and it cannot defeat an interactive check. It makes a
legitimate client look like the browser it claims to be, which is what lets
ordinary pages load instead of being served a challenge page. The
human-in-the-loop parts (interstitial waiting, CAPTCHA solving) live in
:mod:`.captcha`, are opt-in, and are reported honestly.

Honesty rules for this module
-----------------------------
* Only claim what is applied. A capability that is requested but not
  available is reported as unavailable (see :mod:`.mcp_server`).
* Never advertise a codec the client cannot decode. ``Accept-Encoding`` is
  built from the codecs this process can actually decompress, so the server
  never answers with a body we would have to hand back undecoded.
* Never invent a header the real browser would not send. A wrong-but-plausible
  header set is worse than a small one: it is a *fingerprint*.
"""

from __future__ import annotations

import json
import os
import random
import re
import stat
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: The Chrome build this engine claims to be. Kept in one place: the default
#: user agent, the UA-CH brand list and the JS layer must never disagree.
DEFAULT_STEALTH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

#: Chrome's own list of "GREASE" characters, and the brand/version pairs the
#: upstream Obscura JS uses to build ``navigator.userAgentData.brands``.
#: Mirrored here so the ``Sec-CH-UA`` *header* and the *JavaScript* brand
#: list are byte-identical. A header that disagrees with the page it was
#: sent to is one of the cheapest bot tells there is.
_GREASE_CHARS: Tuple[str, ...] = (" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_")
_GREASE_VERS: Tuple[str, ...] = ("8", "99", "24")
_BRAND_PERMS: Tuple[Tuple[int, int, int], ...] = (
    (0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0),
)

#: File name of the persisted profile inside the engine storage directory.
PROFILE_FILENAME = "stealth_profile.json"

#: Screen sizes that actually exist in the wild, per platform family. A
#: fingerprint that reports 3840x2160 on a laptop is a fingerprint.
_SCREENS: Dict[str, Tuple[Tuple[int, int], ...]] = {
    "windows": ((1920, 1080), (1536, 864), (1366, 768), (1600, 900), (2560, 1440)),
    "macos": ((1512, 982), (1728, 1117), (1440, 900), (2560, 1440), (1680, 1050)),
    "linux": ((1920, 1080), (1366, 768), (2560, 1440), (1600, 900)),
}

#: Chrome's own UI height (tab strip + omnibox + bookmark bar), per family.
#: ``innerHeight`` is the screen minus the window chrome; ``outerHeight`` is
#: the screen minus the taskbar.
_UI_HEIGHT: Dict[str, int] = {"windows": 120, "macos": 90, "linux": 80}
_TASKBAR: Dict[str, int] = {"windows": 40, "macos": 0, "linux": 0}

#: Desktop core counts and memory sizes. The upstream engine's non-stealth
#: pools contain values no desktop Chrome reports (``deviceMemory`` of 0.25
#: or 1 is a phone, 2 GB is a ten-year-old laptop).
_CORES: Dict[str, Tuple[int, ...]] = {
    "windows": (4, 8, 12, 16),
    "macos": (8, 10, 12),
    "linux": (4, 8, 12),
}
_MEMORY: Tuple[int, ...] = (4, 8)

#: WebGL vendor/renderer pairs, per family, as Chrome's ANGLE backend
#: reports them. The pair must look like it belongs to the platform:
#: Direct3D11 on Windows, Metal on macOS, OpenGL on Linux.
_GPUS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "windows": (
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ),
    "macos": (
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)"),
        ("Google Inc. (Intel Inc.)", "ANGLE (Intel Inc., ANGLE Metal Renderer: Intel(R) Iris(TM) Plus Graphics, Unspecified Version)"),
    ),
    "linux": (
        ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.6)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 OpenGL 4.6)"),
    ),
}

#: Chrome's `Accept` header for a top-level navigation.
_NAV_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)

#: `Accept` for a subresource script.
_SCRIPT_ACCEPT = "*/*"

_UA_RE = re.compile(r"Chrome/(\d+)\.(\d+)\.(\d+)\.(\d+)")
_EDGE_RE = re.compile(r"Edg/(\d+)\.(\d+)\.(\d+)\.(\d+)")


def chrome_major(user_agent: str) -> int:
    """The Chrome major version claimed by *user_agent* (0 when absent)."""
    match = _UA_RE.search(user_agent or "")
    return int(match.group(1)) if match else 0


def brand_list(major: int, order_seed: Optional[int] = None) -> List[Dict[str, str]]:
    """The UA-CH brand list, in the order this profile reports it.

    The GREASE brand and version come from the Chrome major version, exactly
    as the upstream engine computes them, so the brand list never contradicts
    the user agent. The *order* of the three brands is permuted per profile:
    Chrome randomizes it per session, and one fixed order across every
    request is itself a signal.
    """
    grease_seed = major
    perm_seed = major if order_seed is None else order_seed
    grease = {
        "brand": "Not" + _GREASE_CHARS[grease_seed % 11] + "A"
        + _GREASE_CHARS[(grease_seed + 1) % 11] + "Brand",
        "version": _GREASE_VERS[grease_seed % 3],
    }
    ordered = [
        grease,
        {"brand": "Chromium", "version": str(major)},
        {"brand": "Google Chrome", "version": str(major)},
    ]
    perm = _BRAND_PERMS[perm_seed % len(_BRAND_PERMS)]
    return [dict(ordered[perm[0]]), dict(ordered[perm[1]]), dict(ordered[perm[2]])]


def sec_ch_ua_value(brands: Sequence[Dict[str, str]]) -> str:
    """Render a brand list as a ``Sec-CH-UA`` header value."""
    return ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in brands)


def _family(user_agent: str) -> str:
    """The platform family implied by a user agent string."""
    ua = (user_agent or "").lower()
    if "windows" in ua:
        return "windows"
    if "mac os" in ua or "macintosh" in ua:
        return "macos"
    if "linux" in ua or "x11" in ua or "cros" in ua:
        return "linux"
    return "windows"


def _platform_for(family: str) -> Tuple[str, str, str]:
    """``(navigator.platform, UA-CH platform, UA-CH platformVersion)``."""
    if family == "macos":
        return "MacIntel", "macOS", "15.0.0"
    if family == "linux":
        return "Linux x86_64", "Linux", ""
    return "Win32", "Windows", "15.0.0"


def _language_for(family: str, rng: random.Random) -> Tuple[str, Tuple[str, ...]]:
    """A language pair that matches the platform family."""
    if family == "macos":
        base = rng.choice(("en-US", "en-GB", "de-DE", "fr-FR", "es-ES"))
    elif family == "linux":
        base = rng.choice(("en-US", "en-GB", "de-DE", "pt-BR"))
    else:
        base = rng.choice(("en-US", "en-GB", "en-CA", "en-AU", "de-DE", "fr-FR"))
    return base, (base, base.split("-")[0])


@dataclass(frozen=True)
class StealthProfile:
    """One coherent, stable browser identity."""

    seed: int
    user_agent: str
    family: str
    platform: str
    ua_platform: str
    platform_version: str
    architecture: str
    bitness: str
    model: str
    wow64: bool
    language: str
    languages: Tuple[str, ...]
    brands: Tuple[Dict[str, str], ...]
    screen_width: int
    screen_height: int
    avail_width: int
    avail_height: int
    inner_width: int
    inner_height: int
    outer_width: int
    outer_height: int
    color_depth: int
    device_pixel_ratio: float
    hardware_concurrency: int
    device_memory: int
    max_touch_points: int
    webgl_vendor: str
    webgl_renderer: str
    canvas_seed: int
    effective_type: str
    rtt: int
    downlink: float
    audio_sample_rate: int
    audio_base_latency: float

    # ------------------------------------------------------------- factories
    @classmethod
    def derive(
        cls,
        user_agent: Optional[str] = None,
        *,
        seed: Optional[int] = None,
    ) -> "StealthProfile":
        """Build a profile for *user_agent* from a seed.

        Every field is chosen from a pool that matches the platform the user
        agent claims, so the result is a *possible* machine rather than a
        random pile of values.
        """
        ua = user_agent or DEFAULT_STEALTH_UA
        if _EDGE_RE.search(ua):
            # Edge advertises Edg/ as well as Chrome/; keep the Chrome major
            # (the brand list is Chromium's) but drop the Edge token, which
            # the brand list would otherwise contradict.
            ua = _EDGE_RE.sub("", ua)
        if seed is None:
            seed = random.SystemRandom().randrange(1 << 30)
        rng = random.Random(seed)

        family = _family(ua)
        platform, ua_platform, platform_version = _platform_for(family)
        major = chrome_major(ua) or 145
        language, languages = _language_for(family, rng)

        sw, sh = rng.choice(_SCREENS[family])
        taskbar = _TASKBAR[family]
        ui = _UI_HEIGHT[family]
        outer_h = max(400, sh - taskbar)
        inner_h = max(300, outer_h - ui)

        gpu_vendor, gpu_renderer = rng.choice(_GPUS[family])

        return cls(
            seed=int(seed),
            user_agent=ua,
            family=family,
            platform=platform,
            ua_platform=ua_platform,
            platform_version=platform_version,
            architecture="x86" if family != "macos" else "arm",
            bitness="64",
            model="",
            wow64=False,
            language=language,
            languages=tuple(languages),
            brands=tuple(brand_list(major, order_seed=rng.randrange(1 << 16))),
            screen_width=sw,
            screen_height=sh,
            avail_width=sw,
            avail_height=outer_h,
            inner_width=sw,
            inner_height=inner_h,
            outer_width=sw,
            outer_height=outer_h,
            color_depth=24,
            device_pixel_ratio=1.0,
            hardware_concurrency=rng.choice(_CORES[family]),
            device_memory=rng.choice(_MEMORY),
            max_touch_points=0,
            webgl_vendor=gpu_vendor,
            webgl_renderer=gpu_renderer,
            canvas_seed=rng.randrange(1 << 31),
            effective_type="4g",
            rtt=rng.choice((50, 75, 100, 125)),
            downlink=float(rng.choice((10.0, 8.1, 5.0))),
            # Chrome's audio context: 44.1 kHz or 48 kHz, with a base
            # latency around 10 ms. The engine re-seeds these per page load,
            # so they are part of the profile instead.
            audio_sample_rate=rng.choice((44100, 48000)),
            audio_base_latency=rng.choice((0.01, 0.0053, 0.0125)),
        )

    # ---------------------------------------------------------- serialization
    def to_dict(self) -> Dict[str, object]:
        """JSON-safe representation (used for persistence and the JS layer)."""
        data = asdict(self)
        data["languages"] = list(self.languages)
        data["brands"] = [dict(b) for b in self.brands]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "StealthProfile":
        """Rebuild a profile from :meth:`to_dict` output.

        Unknown keys are ignored and a malformed value falls back to a
        derived profile, so a hand-edited or truncated file cannot produce
        a half-built identity.
        """
        raw_languages = data.get("languages")
        languages: Tuple[str, ...] = (
            tuple(str(item) for item in raw_languages)
            if isinstance(raw_languages, (list, tuple))
            else ("en-US", "en")
        )
        raw_brands = data.get("brands")
        brand_items = raw_brands if isinstance(raw_brands, (list, tuple)) else ()
        brands: Tuple[Dict[str, str], ...] = tuple(
            {
                "brand": str(item.get("brand", "")),
                "version": str(item.get("version", "")),
            }
            for item in brand_items
            if isinstance(item, dict)
        )
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        payload = {
            key: value
            for key, value in data.items()
            if key in known and key not in ("languages", "brands")
        }
        payload["languages"] = languages
        payload["brands"] = brands
        try:
            return cls(**payload)  # type: ignore[arg-type]
        except TypeError:
            # A file from a different profile version: start over rather
            # than run with half an identity.
            return cls.derive(str(data.get("user_agent") or ""))

    # ------------------------------------------------------------- accessors
    @property
    def chrome_major(self) -> int:
        return chrome_major(self.user_agent)

    @property
    def sec_ch_ua(self) -> str:
        return sec_ch_ua_value(self.brands)

    @property
    def sec_ch_ua_mobile(self) -> str:
        return "?1" if "Mobile" in self.user_agent else "?0"

    @property
    def sec_ch_ua_platform(self) -> str:
        return f'"{self.ua_platform}"'

    @property
    def accept_language(self) -> str:
        """The ``Accept-Language`` header for this profile's languages."""
        if len(self.languages) > 1:
            primary, secondary = self.languages[0], self.languages[1]
            return f"{primary},{secondary};q=0.9"
        return self.languages[0] if self.languages else "en-US"

    # ---------------------------------------------------------------- headers
    def navigation_headers(
        self,
        *,
        url: str,
        accept_encoding: str,
        referer: Optional[str] = None,
        extra: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> List[Tuple[str, str]]:
        """Headers for a top-level navigation, in Chrome's order.

        The order matters: HTTP/1.1 servers (and the bot filters in front of
        them) compare the sequence of header names, and Chrome's sequence is
        stable. ``http.client`` sends the list in the order given, so the
        order here is the order on the wire.

        ``Sec-Fetch-Site`` is derived from the referer rather than always
        being ``none``: Chrome sends ``none`` for a URL the user typed, and
        a site relation for a navigation that came from another page.
        """
        headers: List[Tuple[str, str]] = [
            ("sec-ch-ua", self.sec_ch_ua),
            ("sec-ch-ua-mobile", self.sec_ch_ua_mobile),
            ("sec-ch-ua-platform", self.sec_ch_ua_platform),
            ("Upgrade-Insecure-Requests", "1"),
            ("User-Agent", self.user_agent),
            ("Accept", _NAV_ACCEPT),
            ("Sec-Fetch-Site", fetch_site(url, referer)),
            ("Sec-Fetch-Mode", "navigate"),
            ("Sec-Fetch-User", "?1"),
            ("Sec-Fetch-Dest", "document"),
        ]
        if referer:
            headers.append(("Referer", referer))
        headers.extend([
            ("Accept-Encoding", accept_encoding),
            ("Accept-Language", self.accept_language),
            ("Priority", "u=0, i"),
        ])
        if extra:
            headers.extend(extra)
        return headers

    def subresource_headers(
        self,
        url: str,
        *,
        resource_type: str,
        accept_encoding: str,
        referer: Optional[str] = None,
    ) -> List[Tuple[str, str]]:
        """Headers for a subresource fetch, in Chrome's order."""
        dest = {
            "script": "script",
            "image": "image",
            "style": "style",
            "font": "font",
            "xhr": "empty",
            "fetch": "empty",
        }.get(resource_type, "empty")
        mode = "no-cors" if dest in ("script", "image", "style", "font") else "cors"
        accept = _SCRIPT_ACCEPT if dest in ("script", "image", "style", "font") else _NAV_ACCEPT
        headers: List[Tuple[str, str]] = [
            ("sec-ch-ua", self.sec_ch_ua),
            ("sec-ch-ua-mobile", self.sec_ch_ua_mobile),
            ("sec-ch-ua-platform", self.sec_ch_ua_platform),
            ("User-Agent", self.user_agent),
            ("Accept", accept),
            ("Sec-Fetch-Site", fetch_site(url, referer)),
            ("Sec-Fetch-Mode", mode),
            ("Sec-Fetch-Dest", dest),
        ]
        if referer:
            headers.append(("Referer", referer))
        headers.extend([
            ("Accept-Encoding", accept_encoding),
            ("Accept-Language", self.accept_language),
        ])
        return headers

    # -------------------------------------------------------------- js bridge
    def js_payload(self) -> Dict[str, object]:
        """The subset of the profile the page layer needs."""
        return {
            "userAgent": self.user_agent,
            "platform": self.platform,
            "uaPlatform": self.ua_platform,
            "platformVersion": self.platform_version,
            "architecture": self.architecture,
            "bitness": self.bitness,
            "model": self.model,
            "wow64": self.wow64,
            "language": self.language,
            "languages": list(self.languages),
            "brands": [dict(b) for b in self.brands],
            "screenWidth": self.screen_width,
            "screenHeight": self.screen_height,
            "availWidth": self.avail_width,
            "availHeight": self.avail_height,
            "innerWidth": self.inner_width,
            "innerHeight": self.inner_height,
            "outerWidth": self.outer_width,
            "outerHeight": self.outer_height,
            "colorDepth": self.color_depth,
            "devicePixelRatio": self.device_pixel_ratio,
            "hardwareConcurrency": self.hardware_concurrency,
            "deviceMemory": self.device_memory,
            "maxTouchPoints": self.max_touch_points,
            "webglVendor": self.webgl_vendor,
            "webglRenderer": self.webgl_renderer,
            "canvasSeed": self.canvas_seed,
            "effectiveType": self.effective_type,
            "rtt": self.rtt,
            "downlink": self.downlink,
            "audioSampleRate": self.audio_sample_rate,
            "audioBaseLatency": self.audio_base_latency,
        }


def fetch_site(url: str, referer: Optional[str]) -> str:
    """Chrome's ``Sec-Fetch-Site`` for a request against *url* from *referer*."""
    if not referer:
        return "none"
    target = urllib.parse.urlsplit(url)
    source = urllib.parse.urlsplit(referer)
    if not target.hostname or not source.hostname:
        return "cross-site"
    if target.hostname.lower() == source.hostname.lower():
        return "same-origin"
    if _registrable(target.hostname) == _registrable(source.hostname):
        return "same-site"
    return "cross-site"


def _registrable(host: str) -> str:
    """A best-effort registrable domain ("example.co.uk" -> "example.co.uk").

    Not a public-suffix list -- a full PSL is a dependency this project does
    not want -- but good enough for the same-site / cross-site distinction
    that ``Sec-Fetch-Site`` encodes.
    """
    parts = [p for p in (host or "").lower().split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    two_part_suffixes = {"co.uk", "com.au", "co.jp", "co.nz", "com.br", "co.in"}
    tail = ".".join(parts[-2:])
    if tail in two_part_suffixes and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail


# ----------------------------------------------------------------- persistence
def profile_path(storage_dir: Optional[Path]) -> Optional[Path]:
    """Where the profile lives inside *storage_dir* (``None`` when unstored)."""
    if storage_dir is None:
        return None
    return Path(storage_dir) / PROFILE_FILENAME


def load_or_create_profile(
    storage_dir: Optional[Path],
    *,
    user_agent: Optional[str] = None,
    seed: Optional[int] = None,
) -> StealthProfile:
    """Load the persisted profile, or create and persist a new one.

    A browser that changes its fingerprint every time it starts is a browser
    that never goes anywhere. The profile is therefore pinned to the engine's
    storage directory: same machine, same identity, next run.
    """
    path = profile_path(storage_dir)
    if path is not None and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                profile = StealthProfile.from_dict(data)
                # A user agent supplied on the command line wins over the
                # stored one: the operator asked for that identity.
                if user_agent and user_agent != profile.user_agent:
                    return StealthProfile.derive(user_agent, seed=profile.seed)
                return profile
        except Exception:
            # A corrupt profile is not fatal: fall through and make a new one.
            pass
    profile = StealthProfile.derive(user_agent, seed=seed)
    if path is not None:
        save_profile(storage_dir, profile)
    return profile


def save_profile(storage_dir: Optional[Path], profile: StealthProfile) -> None:
    """Persist *profile* into *storage_dir* (best effort)."""
    path = profile_path(storage_dir)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(profile.to_dict(), indent=2).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


__all__ = [
    "DEFAULT_STEALTH_UA",
    "PROFILE_FILENAME",
    "StealthProfile",
    "brand_list",
    "chrome_major",
    "fetch_site",
    "load_or_create_profile",
    "profile_path",
    "save_profile",
    "sec_ch_ua_value",
]
