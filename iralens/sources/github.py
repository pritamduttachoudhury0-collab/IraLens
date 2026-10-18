# -*- coding: utf-8 -*-
"""GitHub — repositories, code search, issues, READMEs.

Ordered backends:
  1. gh-cli — GitHub's official CLI when installed (and optionally authed,
     unlocking private repos and higher rate limits).
  2. github-api — the public REST API anonymously (60 req/h, public data).

Either way the caller gets the same operations and `Artifact`s.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from ..errors import (AuthRequiredError, ExtractionError, OperationUnsupportedError,
                      RateLimitedError,
                      PageUnavailableError, SourceUnavailableError)
from ..model import Artifact
from ..proc import probe_command, run_cli
from ..security import host_matches, public_message
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_API = "https://api.github.com"
_UA = "iralens/0.1"
_TIMEOUT = 20
_MAX_BYTES = 5 * 1024 * 1024
_REPO_RE = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+)")


def _repo_from_url(url: str) -> Optional[Tuple[str, str]]:
    """The ``(owner, repo)`` a github.com URL names, if it names only a repo.

    "https://github.com/<owner>/<repo>" is a repository. A URL with
    anything after the repository — "/issues", "/blob/main/README.md" — is
    a page *of* the repository, and the native reader must not answer it
    with the repository's README: the caller asked for a different page.
    """
    match = _REPO_RE.search(url or "")
    if not match:
        return None
    rest = (url or "")[match.end():].split("?")[0].split("#")[0]
    if rest.strip("/"):
        return None
    return match.group(1), match.group(2).removesuffix(".git")
_GH_ENV = {
    "GH_TELEMETRY": "false",
    "DO_NOT_TRACK": "true",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
}


def _is_rate_limited(exc: urllib.error.HTTPError) -> bool:
    """True when GitHub refused for *rate*, not for *permission*.

    GitHub answers an exhausted anonymous quota with 403 and a body that
    says so; a private or forbidden resource answers 403 with a different
    body. The header is the stronger signal: `X-RateLimit-Remaining: 0`.
    """
    headers = getattr(exc, "headers", None) or {}
    try:
        remaining = headers.get("X-RateLimit-Remaining")
    except AttributeError:  # pragma: no cover - a header mapping without get
        remaining = None
    if remaining is not None and str(remaining).strip() == "0":
        return True
    try:
        body = exc.read().decode("utf-8", errors="replace").lower()
    except Exception:  # pragma: no cover - the body may already be read
        return False
    return "rate limit" in body or "abuse detection" in body


def _rate_limit_detail(exc: urllib.error.HTTPError) -> str:
    """The origin's own words about when the quota comes back."""
    headers = getattr(exc, "headers", None) or {}
    reset = headers.get("X-RateLimit-Reset") if hasattr(headers, "get") else None
    if reset:
        return f"X-RateLimit-Reset: {reset}"
    return "the anonymous GitHub API quota (60 requests an hour per address) is used up"


def _api_get(path: str, accept: str = "application/vnd.github+json") -> Any:
    req = urllib.request.Request(_API + path, headers={"User-Agent": _UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if _is_rate_limited(exc):
            # A rate limit is not an authentication problem. The two need
            # different answers -- "wait" versus "authenticate" -- and
            # reporting one as the other sends the caller to fix something
            # that is not broken. GitHub says which it is: the body says
            # "rate limit exceeded" and/or the rate-limit header is empty.
            raise RateLimitedError(
                "GitHub API rate limit reached",
                detail=_rate_limit_detail(exc),
                hint="wait for the rate limit to reset, or set IRALENS_GITHUB_TOKEN",
            ) from exc
        if exc.code in (401, 403):
            raise AuthRequiredError(
                "GitHub rejected the request (private resource or forbidden)",
                hint="install/auth the GitHub CLI, or set IRALENS_GITHUB_TOKEN",
            ) from exc
        if exc.code == 404:
            raise PageUnavailableError(f"GitHub resource not found: {path}") from exc
        raise PageUnavailableError(f"GitHub API error {exc.code}") from exc
    except Exception as exc:
        raise PageUnavailableError("GitHub API request failed", detail=str(exc)) from exc
    if accept.startswith("application/vnd.github.raw"):
        return body.decode("utf-8", errors="replace")
    return json.loads(body.decode("utf-8"))


def _repo_artifact(repo: Dict[str, Any]) -> Artifact:
    return Artifact(
        title=repo.get("full_name", ""),
        url=repo.get("html_url", ""),
        source="github",
        kind="repo",
        content=(repo.get("description") or "").strip(),
        metadata={
            "stars": repo.get("stargazers_count"),
            "forks": repo.get("forks_count"),
            "language": repo.get("language"),
            "topics": repo.get("topics") or [],
            "open_issues": repo.get("open_issues_count"),
            "updated_at": repo.get("updated_at"),
            "license": (repo.get("license") or {}).get("spdx_id"),
        },
        retrieval_method="api:github",
    )


class GitHubSource(Source):
    name = "github"
    description = "GitHub repositories, code, issues, releases"
    backends = ["gh-cli", "github-api"]
    tier = 0
    operations = {
        "search_repos": {"description": "Search repositories", "params": {"query": "text", "limit": "default 10"}},
        "search_code": {"description": "Search code (needs authenticated gh-cli)", "params": {"query": "text", "limit": "default 10"}},
        "repo": {"description": "Repository details", "params": {"repo": "owner/name"}},
        "readme": {"description": "Repository README", "params": {"repo": "owner/name"}},
        "issues": {"description": "Open issues", "params": {"repo": "owner/name", "limit": "default 10"}},
        "releases": {"description": "Latest releases", "params": {"repo": "owner/name", "limit": "default 5"}},
        "api": {"description": "Raw GitHub REST API GET", "params": {"path": "/repos/owner/name"}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "github.com")

    def health(self, context: "Context") -> SourceHealth:
        probe = probe_command("gh", ["--version"], timeout=10, package="gh", env={**_GH_ENV})
        if probe.ok:
            self.active_backend = "gh-cli"
            return SourceHealth("ok", "GitHub CLI available (public API also works anonymously)", self.active_backend)
        self.active_backend = "github-api"
        return SourceHealth(
            "ok",
            "public GitHub REST API (anonymous, 60 req/h; install gh CLI for private repos and code search)",
            self.active_backend,
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        gh = probe_command("gh", ["--version"], timeout=10, package="gh", env={**_GH_ENV}).ok
        limit = int(params.get("limit") or 10)

        if op == "search_repos":
            self.require(params, "query")
            if gh:
                out = run_cli(
                    ["gh", "search", "repos", params["query"], "--sort", "stars",
                     "--limit", str(limit), "--json", "fullName,url,description,stargazersCount,language,updatedAt"],
                    env_extra=_GH_ENV, timeout=30,
                )
                if out.ok:
                    items = json.loads(out.stdout or "[]")
                    return [
                        Artifact(
                            title=i.get("fullName", ""),
                            url=i.get("url", ""),
                            source="github",
                            kind="repo",
                            content=(i.get("description") or "").strip(),
                            metadata={"stars": i.get("stargazersCount"), "language": i.get("language"),
                                      "updated_at": i.get("updatedAt")},
                            retrieval_method="cli:gh",
                        )
                        for i in items
                    ]
            data = _api_get(f"/search/repositories?q={urllib.parse.quote(params['query'])}"
                            f"&sort=stars&per_page={limit}")
            return [_repo_artifact(r) for r in data.get("items", [])[:limit]]

        if op == "search_code":
            self.require(params, "query")
            if not gh:
                raise AuthRequiredError(
                    "GitHub code search requires the authenticated GitHub CLI",
                    hint="install gh (https://cli.github.com) and run: gh auth login",
                )
            out = run_cli(
                ["gh", "search", "code", params["query"], "--limit", str(limit),
                 "--json", "repository,path,textMatches"],
                env_extra=_GH_ENV, timeout=30,
            )
            if not out.ok:
                raise SourceUnavailableError(public_message(out.stderr.strip()[:300]))
            items = json.loads(out.stdout or "[]")
            return [
                Artifact(
                    title=f"{(i.get('repository') or {}).get('fullName', '')}:{i.get('path', '')}",
                    url=f"https://github.com/{(i.get('repository') or {}).get('fullName', '')}/blob/HEAD/{i.get('path', '')}",
                    source="github",
                    kind="code",
                    content="\n".join(m.get("fragment", "") for m in (i.get("textMatches") or []))[:2000],
                    retrieval_method="cli:gh",
                )
                for i in items
            ]

        if op in ("repo", "readme", "issues", "releases"):
            repo = params.get("repo")
            if not repo:
                raise ExtractionError(f"operation '{op}' requires 'repo' (owner/name)")
            repo = repo.strip("/")
            repo_match = _REPO_RE.match(repo)
            if repo_match:
                repo = "/".join(repo_match.groups())
            if op == "repo":
                return _repo_artifact(_api_get(f"/repos/{repo}"))
            if op == "readme":
                text = _api_get(f"/repos/{repo}/readme", accept="application/vnd.github.raw")
                return Artifact(
                    title=f"{repo} README", url=f"https://github.com/{repo}",
                    source="github", kind="page", content=text[:60000],
                    content_format="markdown", retrieval_method="api:github",
                )
            if op == "issues":
                items = _api_get(f"/repos/{repo}/issues?state=open&per_page={limit}")
                return [
                    Artifact(
                        title=f"#{i.get('number')} {i.get('title', '')}",
                        url=i.get("html_url", ""),
                        source="github", kind="post",
                        content=(i.get("body") or "")[:2000],
                        metadata={"number": i.get("number"), "author": (i.get("user") or {}).get("login"),
                                  "comments": i.get("comments"), "labels": [l.get("name") for l in i.get("labels", [])],
                                  "is_pr": "pull_request" in i},
                        retrieval_method="api:github",
                    )
                    for i in items
                ]
            if op == "releases":
                items = _api_get(f"/repos/{repo}/releases?per_page={limit}")
                return [
                    Artifact(
                        title=r.get("name") or r.get("tag_name", ""),
                        url=r.get("html_url", ""),
                        source="github", kind="post",
                        content=(r.get("body") or "")[:3000],
                        content_format="markdown",
                        metadata={"tag": r.get("tag_name"), "published": r.get("published_at"),
                                  "assets": [a.get("name") for a in r.get("assets", [])]},
                        retrieval_method="api:github",
                    )
                    for r in items
                ]

        if op == "api":
            self.require(params, "path")
            path = str(params["path"])
            if not path.startswith("/"):
                path = "/" + path
            return _api_get(path)

        return super().fetch(op, params, context)

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        owner_and_repo = _repo_from_url(url)
        if owner_and_repo is None:
            raise OperationUnsupportedError(
                "only github.com/<owner>/<repo> URLs are read natively",
                hint="other github.com URLs fall back to the generic web reader in auto mode")
        repo = f"{owner_and_repo[0]}/{owner_and_repo[1]}"
        artifact = self.fetch("repo", {"repo": repo}, context)
        try:
            readme = self.fetch("readme", {"repo": repo}, context)
            artifact.content = (artifact.content + "\n\n" + readme.content)[:60000]
            artifact.content_format = "markdown"
            artifact.metadata["readme_chars"] = len(readme.content)
        except Exception:
            pass  # repo metadata alone is still useful
        return artifact
