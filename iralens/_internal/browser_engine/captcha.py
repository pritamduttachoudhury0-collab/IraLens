# -*- coding: utf-8 -*-
"""Challenges: detection, interstitial waiting, and opt-in CAPTCHA solving.

Three different things get called "a CAPTCHA", and they need three
different answers:

``bot_challenge``
    A JavaScript interstitial (Cloudflare "Just a moment", DataDome,
    Akamai). There is no puzzle: the page's own script computes a token and
    reloads. The right answer is to *behave like a browser that can run
    that script* and then wait for the interstitial to clear itself. This
    module does exactly that, and nothing more.

``captcha``
    An interactive widget (reCAPTCHA, hCaptcha, Turnstile). A person has to
    solve it. IraLens does not forge a token and does not break the
    widget. What it can do is hand the puzzle to a solving service that the
    *operator* has an account with, using the operator's own API key, and
    then enter the answer the way a browser would. That integration is
    opt-in, off by default, and reports exactly what it did.

``auth_required`` / ``access_denied``
    Neither. These are refusals; retrying with a different transport or a
    different identity does not help, and this module does not pretend
    otherwise.

Design rules
------------
* **Detection is the default.** The detection half works with no
  configuration, no key, and no network call to a third party.
* **Solving is explicit.** It requires a provider *and* an API key that
  the operator supplies. Without both, a challenge is reported, not
  bypassed.
* **The operator's key never reaches the page, the URL being read, or a
  log.** It goes to the solving service and nowhere else. (The service's
  own API puts the key in a query string on some calls; that is the
  service's design, not a choice made here.)
* **The answer is reported.** A navigation that met a challenge says so in
  its result, including which provider was involved and whether the page
  was actually reached.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ... import challenge as challenge_mod

#: Re-exported so callers do not need to import two modules.
KIND_NONE = challenge_mod.KIND_NONE
KIND_CAPTCHA = challenge_mod.KIND_CAPTCHA
KIND_BOT_CHALLENGE = challenge_mod.KIND_BOT_CHALLENGE
KIND_AUTH_REQUIRED = challenge_mod.KIND_AUTH_REQUIRED
KIND_ACCESS_DENIED = challenge_mod.KIND_ACCESS_DENIED

#: How the page reports the challenge to the operator.
OUTCOME_NONE = "none"
OUTCOME_DETECTED = "detected"
OUTCOME_CLEARED = "cleared"
OUTCOME_SOLVED = "solved"
OUTCOME_FAILED = "failed"

#: `data-sitekey` / `sitekey` / `k` attributes, per provider.
_SITEKEY_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r'data-sitekey=["\']([A-Za-z0-9_\-]{10,})["\']', ""),
    (r'sitekey["\']?\s*[:=]\s*["\']([A-Za-z0-9_\-]{10,})["\']', ""),
    (r'["\']sitekey["\']\s*:\s*["\']([A-Za-z0-9_\-]{10,})["\']', ""),
    (r'["\']k["\']\s*:\s*["\']([A-Za-z0-9_\-]{10,})["\']', ""),
    (r'googleKey["\']?\s*[:=]\s*["\']([A-Za-z0-9_\-]{10,})["\']', ""),
)

#: Turnstile's callback attribute, used to hand the token back to the page.
_CALLBACK_RE = re.compile(r'data-callback=["\']([A-Za-z0-9_$]+)["\']')


@dataclass
class ChallengeInfo:
    """One detected challenge."""

    kind: str = KIND_NONE
    provider: str = ""
    marker: str = ""
    sitekey: str = ""
    callback: str = ""
    page_url: str = ""
    http_status: Optional[int] = None
    confident: bool = False
    #: What the engine did about it: nothing, cleared, solved, failed.
    outcome: str = OUTCOME_NONE
    #: A human-readable note about what happened (never page content).
    note: str = ""
    #: The solving service that was used, when one was.
    solver: str = ""

    @property
    def is_captcha(self) -> bool:
        return self.kind == KIND_CAPTCHA

    @property
    def is_interstitial(self) -> bool:
        return self.kind == KIND_BOT_CHALLENGE

    @property
    def resolved(self) -> bool:
        """Whether the page was actually reached after the challenge."""
        return self.outcome in (OUTCOME_CLEARED, OUTCOME_SOLVED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "marker": self.marker,
            "sitekey": self.sitekey,
            "page_url": self.page_url,
            "http_status": self.http_status,
            "confident": self.confident,
            "outcome": self.outcome,
            "note": self.note,
            "solver": self.solver,
        }


class _ReportVerdict:
    """Adapts a report dict to the ``verdict`` interface the errors use."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = dict(data)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


def verdict_from_dict(data: Dict[str, Any]) -> _ReportVerdict:
    """Wrap a challenge report so it can travel in a typed error."""
    return _ReportVerdict(data)


def extract_sitekey(html: str) -> str:
    """Pull a widget site key out of a challenge page (empty when absent)."""
    text = html or ""
    for pattern, _ in _SITEKEY_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def extract_callback(html: str) -> str:
    """Pull the widget's callback name out of a challenge page."""
    match = _CALLBACK_RE.search(html or "")
    return match.group(1) if match else ""


def detect(
    html: Optional[str] = None,
    *,
    text: Optional[str] = None,
    url: str = "",
    http_status: Optional[int] = None,
) -> Optional[ChallengeInfo]:
    """Detect a challenge in a page, or return ``None``.

    Reuses the shared detector (:mod:`iralens.challenge`) so the browser tier
    and the static reader agree on what counts as a challenge.
    """
    body = (html or "").encode("utf-8", errors="ignore") if html is not None else None
    verdict = challenge_mod.detect(
        body, text=text, http_status=http_status, headers=None
    )
    if verdict.kind == KIND_NONE:
        return None
    sitekey = extract_sitekey(html or "")
    return ChallengeInfo(
        kind=verdict.kind,
        provider=verdict.provider,
        marker=verdict.marker,
        sitekey=sitekey,
        callback=extract_callback(html or ""),
        page_url=url,
        http_status=http_status,
        confident=verdict.confident,
    )


# --------------------------------------------------------------------- solvers

class SolverError(RuntimeError):
    """A solving attempt failed."""


class Solver:
    """Base class for CAPTCHA solving back ends."""

    #: The provider name, as configured.
    name = "none"

    #: Whether this solver can actually run.
    available = False

    def solve(self, info: ChallengeInfo, *, timeout: float = 120.0) -> str:
        """Return a token for *info*, or raise :class:`SolverError`."""
        raise SolverError(f"{self.name} solver cannot solve challenges")

    def describe(self) -> str:
        """One line describing what this solver does (for reports)."""
        return self.name


class NullSolver(Solver):
    """The default: no solving service is configured.

    It exists so the calling code has one shape to handle, and so the
    engine can say "a challenge was detected and no solver was configured"
    instead of pretending the page loaded.
    """

    name = "none"
    available = False

    def solve(self, info: ChallengeInfo, *, timeout: float = 120.0) -> str:
        raise SolverError(
            "no CAPTCHA solver is configured; set captcha_provider and "
            "captcha_api_key to enable one"
        )

    def describe(self) -> str:
        return "no solver configured"


def _post_json(url: str, payload: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise SolverError(f"unexpected response from {url}")
    return parsed


def _get_json(url: str, *, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise SolverError(f"unexpected response from {url}")
    return parsed


class TwoCaptchaSolver(Solver):
    """Delegate to a 2Captcha account (the operator's own key).

    2Captcha's task types are provider-specific; the mapping below covers
    the widgets this module can detect.
    """

    name = "2captcha"
    available = True

    ENDPOINT = "https://api.2captcha.com"

    #: Widget -> 2Captcha method.
    METHODS = {
        "recaptcha": "userrecaptcha",
        "hcaptcha": "hcaptcha",
        "turnstile": "turnstile",
    }

    def __init__(self, api_key: str, *, timeout: float = 120.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def describe(self) -> str:
        return "2captcha (operator's account)"

    def _method_for(self, info: ChallengeInfo) -> str:
        provider = (info.provider or "").lower()
        if provider in self.METHODS:
            return self.METHODS[provider]
        if info.sitekey:
            return self.METHODS["recaptcha"]
        raise SolverError(f"2captcha cannot handle a {provider or 'unknown'} challenge")

    def solve(self, info: ChallengeInfo, *, timeout: float = 120.0) -> str:
        if not info.sitekey:
            raise SolverError("the challenge page has no site key to solve")
        method = self._method_for(info)
        payload = {
            "key": self.api_key,
            "method": method,
            "pageurl": info.page_url,
        }
        if method == "userrecaptcha":
            payload["googleKey"] = info.sitekey
        elif method == "hcaptcha":
            payload["sitekey"] = info.sitekey
        elif method == "turnstile":
            payload["sitekey"] = info.sitekey
        try:
            created = _post_json(self.ENDPOINT + "/createTask", payload, timeout=30.0)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SolverError(f"2captcha createTask failed: {exc}") from exc
        task_id = str(created.get("requestId") or created.get("taskId") or "")
        if not task_id:
            raise SolverError(f"2captcha did not return a task id: {created.get('request')}")
        return self._await(task_id, timeout=timeout)

    def _await(self, task_id: str, *, timeout: float) -> str:
        deadline = time.monotonic() + max(5.0, timeout)
        query = urllib.parse.urlencode({"key": self.api_key, "taskId": task_id, "json": 1})
        while time.monotonic() < deadline:
            try:
                result = _get_json(f"{self.ENDPOINT}/getTaskResult?{query}", timeout=30.0)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise SolverError(f"2captcha getTaskResult failed: {exc}") from exc
            status = result.get("status")
            if status == 1:
                solution = result.get("solution") or {}
                token = str(solution.get("gRecaptchaResponse") or solution.get("token") or "")
                if not token:
                    raise SolverError("2captcha returned an empty solution")
                return token
            if result.get("request") and not status:
                raise SolverError(f"2captcha rejected the task: {result.get('request')}")
            time.sleep(5.0)
        raise SolverError("2captcha did not answer in time")


class AntiCaptchaSolver(Solver):
    """Delegate to an Anti-Captcha account (the operator's own key)."""

    name = "anticaptcha"
    available = True

    ENDPOINT = "https://api.anti-captcha.com"

    def __init__(self, api_key: str, *, timeout: float = 120.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def describe(self) -> str:
        return "anti-captcha (operator's account)"

    def solve(self, info: ChallengeInfo, *, timeout: float = 120.0) -> str:
        if not info.sitekey:
            raise SolverError("the challenge page has no site key to solve")
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "NoCaptchaTaskProxyless",
                "websiteURL": info.page_url,
                "websiteKey": info.sitekey,
            },
        }
        try:
            created = _post_json(self.ENDPOINT + "/createTask", payload, timeout=30.0)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SolverError(f"anti-captcha createTask failed: {exc}") from exc
        task_id = str(created.get("taskId") or "")
        if not task_id:
            raise SolverError(f"anti-captcha did not return a task id: {created.get('errorDescription')}")
        return self._await(task_id, timeout=timeout)

    def _await(self, task_id: str, *, timeout: float) -> str:
        deadline = time.monotonic() + max(5.0, timeout)
        while time.monotonic() < deadline:
            try:
                result = _post_json(
                    self.ENDPOINT + "/getTaskResult",
                    {"clientKey": self.api_key, "taskId": task_id},
                    timeout=30.0,
                )
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise SolverError(f"anti-captcha getTaskResult failed: {exc}") from exc
            if result.get("errorCode"):
                raise SolverError(
                    f"anti-captcha reported {result.get('errorCode')}: {result.get('errorDescription')}"
                )
            status = result.get("status")
            if status == "ready":
                solution = result.get("solution") or {}
                token = str(solution.get("gRecaptchaResponse") or solution.get("token") or "")
                if not token:
                    raise SolverError("anti-captcha returned an empty solution")
                return token
            time.sleep(5.0)
        raise SolverError("anti-captcha did not answer in time")


#: Provider name -> solver class.
SOLVERS: Dict[str, type] = {
    "2captcha": TwoCaptchaSolver,
    "two-captcha": TwoCaptchaSolver,
    "anticaptcha": AntiCaptchaSolver,
    "anti-captcha": AntiCaptchaSolver,
}


def build_solver(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    *,
    timeout: float = 120.0,
) -> Solver:
    """Build the solver named by *provider*, using the operator's *api_key*.

    Returns a :class:`NullSolver` when the provider is missing, unknown, or
    has no key -- a challenge is then reported, never bypassed.
    """
    name = (provider or "").strip().lower()
    key = (api_key or "").strip()
    if not name or name in ("none", "off", "disabled"):
        return NullSolver()
    if not key:
        return NullSolver()
    solver_cls = SOLVERS.get(name)
    if solver_cls is None:
        return NullSolver()
    return solver_cls(key, timeout=timeout)


# --------------------------------------------------------------- token entry

#: The JavaScript that hands a solved token back to the page.
_TOKEN_SCRIPT = r"""
(function(token, kind, callbackName) {
  var selectors = {
    recaptcha: 'textarea[name="g-recaptcha-response"], input[name="g-recaptcha-response"]',
    hcaptcha: 'textarea[name="h-captcha-response"], input[name="h-captcha-response"]',
    turnstile: 'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]',
  };
  var selector = selectors[kind] || selectors.recaptcha;
  var placed = false;
  try {
    var field = document.querySelector(selector);
    if (field) {
      field.value = token;
      field.setAttribute('value', token);
      try { field.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
      try { field.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) {}
      placed = true;
    }
  } catch (e) {}
  var called = false;
  if (callbackName) {
    try {
      var fn = globalThis[callbackName];
      if (typeof fn === 'function') { fn(token); called = true; }
    } catch (e) {}
  }
  if (!called) {
    // Widgets without a callback still need the page to notice the answer.
    try {
      var form = document.querySelector('form');
      if (form) {
        var submitter = form.querySelector('input[type="submit"], button[type="submit"]');
        if (submitter) { submitter.click(); called = true; }
      }
    } catch (e) {}
  }
  return JSON.stringify({ placed: placed, callback: called });
})(%s, %s, %s)
"""


def token_injection_script(token: str, info: ChallengeInfo) -> str:
    """The JavaScript that enters a solved token into the page."""
    kind = {
        "recaptcha": "recaptcha",
        "hcaptcha": "hcaptcha",
        "turnstile": "turnstile",
    }.get((info.provider or "").lower(), "recaptcha")
    return _TOKEN_SCRIPT % (
        json.dumps(token),
        json.dumps(kind),
        json.dumps(info.callback or ""),
    )


__all__ = [
    "AntiCaptchaSolver",
    "ChallengeInfo",
    "NullSolver",
    "OUTCOME_CLEARED",
    "OUTCOME_DETECTED",
    "OUTCOME_FAILED",
    "OUTCOME_NONE",
    "OUTCOME_SOLVED",
    "SOLVERS",
    "Solver",
    "SolverError",
    "TwoCaptchaSolver",
    "build_solver",
    "detect",
    "extract_callback",
    "extract_sitekey",
    "token_injection_script",
]
