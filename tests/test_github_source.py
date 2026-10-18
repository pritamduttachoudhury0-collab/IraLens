# -*- coding: utf-8 -*-
"""The GitHub adapter has to say *which* refusal it is.

GitHub answers an exhausted anonymous quota with 403 and a body that says
"rate limit exceeded"; it answers a forbidden or private resource with 403
and a different body; it answers a missing credential with 401. The first
needs the answer "wait", the other two need "authenticate" -- and a caller
that is told "authenticate" for a rate limit goes off to fix something that
is not broken.

This was found by the release's own CI: the runner's shared address
exhausted the anonymous quota, and the adapter reported it as an
authentication problem.
"""

import io
import urllib.error

import pytest

from iralens.errors import AuthRequiredError, PageUnavailableError, RateLimitedError
from iralens.sources.github import _api_get


def _http_error(code, body="", headers=None):
    return urllib.error.HTTPError(
        url="https://api.github.com/search/repositories?q=x",
        code=code,
        msg="error",
        hdrs=headers or {},
        fp=io.BytesIO(body.encode("utf-8")),
    )


def _raise(error, monkeypatch):
    def _boom(*args, **kwargs):
        raise error

    monkeypatch.setattr("iralens.sources.github.urllib.request.urlopen", _boom)


def test_a_rate_limited_api_is_a_rate_limit_not_an_authentication_problem(monkeypatch):
    _raise(_http_error(403, '{"message":"API rate limit exceeded"}',
                       {"X-RateLimit-Remaining": "0"}), monkeypatch)
    with pytest.raises(RateLimitedError) as exc:
        _api_get("/search/repositories?q=x")
    assert exc.value.error_type == "rate_limited"
    assert not isinstance(exc.value, AuthRequiredError)


def test_a_rate_limit_is_recognised_from_the_body_alone(monkeypatch):
    """Not every client sends the header; the body says it too."""
    _raise(_http_error(403, '{"message":"You have exceeded a secondary rate limit"}',
                       {"X-RateLimit-Remaining": "10"}), monkeypatch)
    with pytest.raises(RateLimitedError):
        _api_get("/search/repositories?q=x")


def test_a_forbidden_resource_is_still_an_authentication_problem(monkeypatch):
    _raise(_http_error(403, '{"message":"Resource not accessible by integration"}'), monkeypatch)
    with pytest.raises(AuthRequiredError):
        _api_get("/search/repositories?q=x")


def test_an_unauthorized_request_is_an_authentication_problem(monkeypatch):
    _raise(_http_error(401, '{"message":"Requires authentication"}'), monkeypatch)
    with pytest.raises(AuthRequiredError):
        _api_get("/search/repositories?q=x")


def test_a_missing_resource_is_a_missing_resource(monkeypatch):
    _raise(_http_error(404, '{"message":"Not Found"}'), monkeypatch)
    with pytest.raises(PageUnavailableError) as exc:
        _api_get("/search/repositories?q=x")
    assert not isinstance(exc.value, RateLimitedError)
