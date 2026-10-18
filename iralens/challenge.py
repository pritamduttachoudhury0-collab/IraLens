# -*- coding: utf-8 -*-
"""Detection of interactive-verification and access-block interstitials.

A rendered challenge page is **not** research content. Returning one as if it
were a successful read is the worst failure mode in this area: the agent
receives a plausible-looking artifact whose text says "verify you are human"
and treats it as evidence. This module exists to make that state explicit,
machine-readable, and non-retryable.

Design rules
------------
* **Detection only.** Nothing here circumvents, solves, or defeats anything.
  We classify the page and get out of the way.
* **Conservative.** A false positive turns a perfectly readable page into a
  failure, so markers are specific widget/script identifiers and
  interstitial phrasing, never a bare word like "captcha" in running prose.
* **Corroboration.** Weak markers only count when a second independent signal
  is present (a status code, a provider reference id, or a second marker).
* **Honest providers.** Where the interstitial is recognisable it is named
  (cloudflare / datadome / akamai / hcaptcha / recaptcha / turnstile); when it
  is not, ``provider`` stays empty rather than guessing.

What this deliberately does NOT do
----------------------------------
It does not solve CAPTCHAs, mint or forge challenge tokens, replay sessions,
rotate identities to get another attempt, or interfere with verification
endpoints. Blocking trackers (see ``--block-trackers``) never blocks a
CAPTCHA or verification resource.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

#: No interstitial detected; the content is whatever it is.
KIND_NONE = "none"
#: A CAPTCHA widget is present (reCAPTCHA / hCaptcha / Turnstile).
KIND_CAPTCHA = "captcha"
#: A JavaScript or browser-check interstitial (Cloudflare "Just a moment", etc).
KIND_BOT_CHALLENGE = "bot_challenge"
#: The resource needs credentials the caller has not supplied.
KIND_AUTH_REQUIRED = "auth_required"
#: The origin refused access (403 / block page) in a way retries won't fix.
KIND_ACCESS_DENIED = "access_denied"

#: Bytes of a response body inspected for markers. Interstitials announce
#: themselves in the head/first screen, so scanning the whole body only adds
#: false-positive surface.
_SCAN_BYTES = 64 * 1024


@dataclass(frozen=True)
class ChallengeVerdict:
    """The result of inspecting one response."""

    kind: str = KIND_NONE
    provider: str = ""
    #: The specific marker that fired, for diagnostics.
    marker: str = ""
    http_status: Optional[int] = None
    #: True when the verdict is confident enough to act on (fail the read).
    confident: bool = False
    challenge: bool = False

    @property
    def detected(self) -> bool:
        return self.kind != KIND_NONE

    def to_dict(self) -> Dict[str, object]:
        data: Dict[str, object] = {
            "kind": self.kind,
            "provider": self.provider,
            "confident": self.confident,
        }
        if self.marker:
            data["marker"] = self.marker
        if self.http_status is not None:
            data["http_status"] = self.http_status
        return data


# --------------------------------------------------------------------- markers
# High-signal: a real widget or a provider's own script/endpoint. These are
# identifiers, not prose, so they do not appear in ordinary article text.
_CAPTCHA_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("g-recaptcha", "recaptcha"),
    ("recaptcha/api.js", "recaptcha"),
    ("google.com/recaptcha", "recaptcha"),
    ("recaptcha.net/recaptcha", "recaptcha"),
    ("hcaptcha.com/1/api.js", "hcaptcha"),
    ("hcaptcha_widget", "hcaptcha"),
    ("data-hcaptcha-sitekey", "hcaptcha"),
    ("challenges.cloudflare.com/turnstile", "turnstile"),
    ("cf-turnstile", "turnstile"),
    ("turnstile.do/turnstile", "turnstile"),
)

# Provider challenge/interstitial scripts. Decisive like a widget identifier,
# but a different situation: there is no puzzle a person solves here, the
# page is gated behind a browser check. Callers need to tell these apart —
# a captcha is something a human can clear; an interstitial is a refusal
# that retrying will not fix.
_CHALLENGE_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("cf_chl_opt", "cloudflare"),
    ("cdn-cgi/challenge-platform", "cloudflare"),
    ("__cf_chl_", "cloudflare"),
    ("_incapsula_resource", "imperva"),
)

# Interstitial phrasing. Weaker than widget identifiers because marketing
# copy can contain similar words, so these require corroboration.
_BOT_PHRASES: Tuple[str, ...] = (
    "just a moment",
    "checking your browser before accessing",
    "checking if the site connection is secure",
    "verifying you are human",
    "please enable javascript and cookies to continue",
    "enable javascript and cookies to continue",
    "performing security verification",
    "attention required",
    "ddos protection by",
    "please complete the security check",
    "unusual traffic from your computer network",
    "request blocked by administrative rules",
    "automated access is prohibited",
)

_PROVIDER_FINGERPRINTS: Tuple[Tuple[str, str], ...] = (
    ("cf-ray", "cloudflare"),
    ("cloudflare", "cloudflare"),
    ("_cfuvid", "cloudflare"),
    ("datadome", "datadome"),
    ("akamai", "akamai"),
    ("reference #", "akamai"),
    ("imperva", "imperva"),
    ("incapsula", "imperva"),
    ("x-datadome", "datadome"),
)

_AUTH_PHRASES: Tuple[str, ...] = (
    "sign in to continue",
    "log in to continue",
    "please log in to view",
    "authentication required",
    "you must be logged in",
    "session has expired",
)

_DENIED_PHRASES: Tuple[str, ...] = (
    "access denied",
    "you don't have permission",
    "you do not have permission",
    "403 forbidden",
    "request blocked",
)

# Reference-id patterns that corroborate a block page ("Ray ID", "Reference #").
_REF_ID_RE = re.compile(
    r"(?:ray\s*id|reference\s*(?:#|id)|incident\s*id|request\s*id)\s*[:#]?\s*[0-9a-f\-]{6,}",
    re.IGNORECASE,
)


def _sample(body: bytes) -> str:
    return (body or b"")[:_SCAN_BYTES].decode("utf-8", errors="ignore").casefold()


def _text_sample(text: str) -> str:
    return (text or "")[:_SCAN_BYTES].casefold()


def _match_any(sample: str, needles: Tuple[str, ...]) -> str:
    for needle in needles:
        if needle in sample:
            return needle
    return ""


def detect(body: Optional[bytes] = None, *, text: Optional[str] = None,
           http_status: Optional[int] = None,
           headers: Optional[Dict[str, object]] = None) -> ChallengeVerdict:
    """Classify a response as an interstitial, or ``KIND_NONE``.

    ``body`` is the raw response bytes (HTML path); ``text`` is already
    extracted/markdown text (browser path). Supply whichever you have — the
    rendered-text form matters because a challenge page that renders
    successfully must still not be mistaken for content.
    """
    sample = _sample(body) if body is not None else _text_sample(text or "")
    if not sample.strip():
        return ChallengeVerdict(http_status=http_status)

    header_blob = " ".join(
        f"{k} {v}" for k, v in (headers or {}).items()
    ).casefold()

    # 1. A real CAPTCHA widget. This is an identifier, so it is decisive.
    for marker, provider in _CAPTCHA_MARKERS:
        if marker in sample:
            return ChallengeVerdict(kind=KIND_CAPTCHA, provider=provider,
                                    marker=marker, http_status=http_status,
                                    confident=True, challenge=True)

    # 2. A provider's own challenge script. Also decisive, and reported as a
    #    bot challenge rather than a captcha because there is no widget here.
    for marker, provider in _CHALLENGE_MARKERS:
        if marker in sample:
            return ChallengeVerdict(kind=KIND_BOT_CHALLENGE, provider=provider,
                                    marker=marker, http_status=http_status,
                                    confident=True, challenge=True)

    # 3. Bot-check interstitial. Phrasing alone is weak; require a second,
    #    independent signal: a provider fingerprint, a reference id, or a
    #    status code that means refusal.
    phrase = _match_any(sample, _BOT_PHRASES)
    if phrase:
        provider = ""
        for needle, name in _PROVIDER_FINGERPRINTS:
            if needle in sample or needle in header_blob:
                provider = name
                break
        corroborated = bool(provider) or bool(_REF_ID_RE.search(sample)) or \
            http_status in (403, 429, 503)
        return ChallengeVerdict(kind=KIND_BOT_CHALLENGE, provider=provider,
                                marker=phrase, http_status=http_status,
                                confident=corroborated, challenge=True)

    # 4. Authentication wall.
    auth = _match_any(sample, _AUTH_PHRASES)
    if auth and (http_status == 401 or "login" in sample or "signin" in sample
                 or "session" in sample):
        return ChallengeVerdict(kind=KIND_AUTH_REQUIRED, marker=auth,
                                http_status=http_status, confident=True,
                                challenge=True)

    # 5. Access denied. Only with a refusing status or a provider reference,
    #    so an article that merely quotes "access denied" is not misread.
    denied = _match_any(sample, _DENIED_PHRASES)
    if denied and (http_status in (401, 403) or bool(_REF_ID_RE.search(sample))
                   or any(n in sample or n in header_blob
                          for n, _ in _PROVIDER_FINGERPRINTS)):
        provider = ""
        for needle, name in _PROVIDER_FINGERPRINTS:
            if needle in sample or needle in header_blob:
                provider = name
                break
        return ChallengeVerdict(kind=KIND_ACCESS_DENIED, provider=provider,
                                marker=denied, http_status=http_status,
                                confident=True, challenge=True)

    return ChallengeVerdict(http_status=http_status)


#: Status codes that mean "a human or different credentials are needed", not
#: "try again with another transport".
VERIFICATION_STATUSES = frozenset({401, 403})


def is_verification_status(status: Optional[int]) -> bool:
    """Whether a status code should be reported as verification-required."""
    return status in VERIFICATION_STATUSES


__all__ = [
    "ChallengeVerdict",
    "KIND_ACCESS_DENIED",
    "KIND_AUTH_REQUIRED",
    "KIND_BOT_CHALLENGE",
    "KIND_CAPTCHA",
    "KIND_NONE",
    "detect",
    "is_verification_status",
]
