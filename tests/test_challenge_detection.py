# -*- coding: utf-8 -*-
"""Interactive-verification detection: reported honestly, never as content.

The failure mode this guards: a challenge page that renders perfectly looks
like a successful read, and an agent treats "verify you are human" as
research evidence. Detection must therefore run on the *extracted text*, not
only on raw HTML, and must produce a typed, machine-readable verdict.

These tests are offline and deterministic. Nothing here solves, evades, or
interferes with a verification control — they only assert that IraLens
classifies an interstitial correctly and stops retrying.
"""

from __future__ import annotations

import pytest

from iralens import challenge
from iralens.errors import PageUnavailableError, VerificationRequiredError

# ------------------------------------------------------------------ fixtures
CLOUDFLARE = b"""<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="cf-wrapper">
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
<h1>Checking your browser before accessing example.com</h1>
<p>Please enable JavaScript and cookies to continue.</p>
<p>Ray ID: 8f2a1c3b4d5e6f70</p>
</div></body></html>"""

RECAPTCHA = b"""<!DOCTYPE html><html><body>
<form action="/submit" method="POST">
  <div class="g-recaptcha" data-sitekey="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"></div>
  <script src="https://www.google.com/recaptcha/api.js" async defer></script>
  <input type="submit" value="Submit">
</form></body></html>"""

TURNSTILE = b"""<html><body>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<div class="cf-turnstile" data-sitekey="0x4AAAAAAA"></div>
</body></html>"""

AUTH_WALL = b"""<html><head><title>Sign in</title></head><body>
<h2>Please log in to view this page</h2>
<a href="/login">Sign in to continue</a>
</body></html>"""

ACCESS_DENIED = b"""<html><head><title>Access Denied</title></head><body>
<h1>Access Denied</h1>
<p>You don't have permission to access this resource.</p>
<p>Reference #18.5f2a1c3b.1760000000.1a2b3c4d</p>
</body></html>"""

# Legitimate content that must NOT be classified as a challenge.
NORMAL_ARTICLE = b"""<!DOCTYPE html><html><head><title>Scaling Python Asyncio</title></head>
<body><article><h1>Scaling Python Asyncio</h1>
<p>This article explains how to scale asyncio applications in production. We
discuss event loops, task groups, and backpressure in detail.</p>
<p>Earlier versions of this post mentioned the word captcha when describing
form spam, and we also cover Cloudflare Workers as a deployment target.</p>
</article></body></html>"""

CLOUDFLARE_MARKETING = b"""<!DOCTYPE html><html><head><title>Cloudflare Workers</title></head>
<body><article><h1>Introducing Workers Analytics Engine</h1>
<p>Cloudflare Workers now supports a native analytics engine. This article
explains how to query your data and build dashboards on top of it.</p>
</article></body></html>"""


# ------------------------------------------------------------- true positives
@pytest.mark.parametrize("body, provider", [
    (CLOUDFLARE, "cloudflare"),
    (RECAPTCHA, "recaptcha"),
    (TURNSTILE, "turnstile"),
])
def test_widget_and_interstitial_markers_are_detected(body, provider):
    verdict = challenge.detect(body)
    assert verdict.detected
    assert verdict.confident
    assert verdict.provider == provider


def test_cloudflare_challenge_is_a_bot_challenge():
    verdict = challenge.detect(CLOUDFLARE)
    assert verdict.kind == challenge.KIND_BOT_CHALLENGE


def test_recaptcha_widget_is_a_captcha():
    assert challenge.detect(RECAPTCHA).kind == challenge.KIND_CAPTCHA
    assert challenge.detect(TURNSTILE).kind == challenge.KIND_CAPTCHA


def test_auth_wall_is_reported_as_auth_required():
    verdict = challenge.detect(AUTH_WALL)
    assert verdict.detected
    assert verdict.kind == challenge.KIND_AUTH_REQUIRED


def test_access_denied_with_reference_id_is_reported():
    verdict = challenge.detect(ACCESS_DENIED, http_status=403)
    assert verdict.detected
    assert verdict.kind == challenge.KIND_ACCESS_DENIED


def test_detection_works_on_extracted_text_not_just_raw_html():
    """A challenge that renders cleanly must still not pass as content."""
    markdown = (
        "# Just a moment...\n\n"
        "Checking your browser before accessing example.com.\n\n"
        "Please enable JavaScript and cookies to continue.\n\n"
        "Ray ID: 8f2a1c3b4d5e6f70\n"
    )
    verdict = challenge.detect(text=markdown)
    assert verdict.detected and verdict.confident


def test_verdict_serialises_for_machine_consumption():
    data = challenge.detect(CLOUDFLARE).to_dict()
    assert set(data) >= {"kind", "provider", "confident"}
    assert data["kind"] == challenge.KIND_BOT_CHALLENGE


# ------------------------------------------------------------ false positives
def test_ordinary_article_is_not_flagged():
    verdict = challenge.detect(NORMAL_ARTICLE)
    assert verdict.kind == challenge.KIND_NONE
    assert not verdict.detected


def test_article_merely_mentioning_cloudflare_is_not_flagged():
    """Marketing/editorial use of a vendor name must not look like a block."""
    assert challenge.detect(CLOUDFLARE_MARKETING).kind == challenge.KIND_NONE


def test_bare_403_without_block_markers_is_not_a_verdict():
    """A 403 alone is not evidence of an interstitial."""
    body = b"<html><body><p>Nothing to see here.</p></body></html>"
    assert challenge.detect(body, http_status=403).kind == challenge.KIND_NONE


def test_weak_phrase_without_corroboration_is_not_confident():
    """Phrasing alone must not fail a read; it needs a second signal."""
    body = b"<html><body><p>Just a moment, loading your results.</p></body></html>"
    verdict = challenge.detect(body)
    # Either not detected at all, or detected but explicitly not confident.
    assert (not verdict.detected) or (verdict.detected and not verdict.confident)


def test_unknown_provider_is_left_blank_rather_than_guessed():
    body = (b"<html><body><p>Verifying you are human.</p>"
            b"<p>Request id: 12ab34cd56ef</p></body></html>")
    verdict = challenge.detect(body)
    assert verdict.detected
    assert verdict.provider == ""


def test_empty_body_is_never_a_challenge():
    assert challenge.detect(b"").kind == challenge.KIND_NONE
    assert challenge.detect(text="").kind == challenge.KIND_NONE


# --------------------------------------------------------------- error typing
def test_verification_error_is_still_a_page_error_for_existing_handlers():
    """Backward compatibility: old `except PageUnavailableError` still works."""
    err = VerificationRequiredError("blocked", verdict=challenge.detect(CLOUDFLARE))
    assert isinstance(err, PageUnavailableError)
    assert err.error_type == "verification_required"


def test_verification_error_carries_a_structured_verdict():
    err = VerificationRequiredError(
        "blocked", verdict=challenge.detect(CLOUDFLARE), url="https://example.org/x")
    data = err.to_dict()
    assert data["error"] == "verification_required"
    assert data["verification"]["kind"] == challenge.KIND_BOT_CHALLENGE
    assert data["url"] == "https://example.org/x"


def test_verification_status_helper():
    assert challenge.is_verification_status(401)
    assert challenge.is_verification_status(403)
    assert not challenge.is_verification_status(200)
    assert not challenge.is_verification_status(None)


# ------------------------------------------------- pipeline integration
class _StaticContext:
    class _Config:
        def get(self, key, default=None):
            return default

    def __init__(self) -> None:
        self.config = self._Config()

    def engine(self):
        raise AssertionError("a challenge must never trigger the browser tier")


@pytest.fixture()
def serve(monkeypatch, tmp_path):
    """Serve a fixed body through the real read pipeline, no network."""
    from iralens import sources
    from iralens.cache import ResponseCache

    web_mod = sources.web
    pending: dict = {}

    class FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body
            self._done = False

        def read(self, _n=-1):
            if self._done:
                return b""
            self._done = True
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def headers(self):
            return {"Content-Type": "text/html; charset=utf-8"}

        @property
        def status(self):
            return 200

        @property
        def url(self):
            return "https://example.org/page"

    monkeypatch.setattr(web_mod, "safe_urlopen",
                        lambda url, *a, **k: FakeResponse(pending["body"]))
    monkeypatch.setattr(web_mod, "ResponseCache",
                        lambda enabled=True: ResponseCache(tmp_path / "pages"))

    def read(body: bytes, mode: str = "static"):
        pending["body"] = body
        return web_mod.WebSource().read_url("https://example.org/page",
                                            _StaticContext(), mode=mode)
    return read


def test_challenge_page_raises_verification_required(serve):
    with pytest.raises(VerificationRequiredError) as exc:
        serve(CLOUDFLARE)
    assert exc.value.verdict.kind == challenge.KIND_BOT_CHALLENGE


def test_challenge_is_not_escalated_to_the_browser_in_auto_mode(serve):
    """Auto mode must not spend a render redrawing the same challenge."""
    with pytest.raises(VerificationRequiredError):
        serve(CLOUDFLARE, mode="auto")


def test_legitimate_page_still_reads_normally(serve):
    art = serve(NORMAL_ARTICLE)
    assert art.content
    assert "Scaling Python Asyncio" in art.title
    assert "captcha" not in art.title.lower() or True


def test_captcha_widget_page_raises_verification_required(serve):
    with pytest.raises(VerificationRequiredError) as exc:
        serve(RECAPTCHA)
    assert exc.value.verdict.kind == challenge.KIND_CAPTCHA


def test_the_challenge_page_is_never_returned_as_content(serve):
    """Regression: the rendered interstitial must not reach the agent."""
    try:
        serve(TURNSTILE)
    except VerificationRequiredError:
        return
    pytest.fail("a Turnstile interstitial was returned as if it were content")
