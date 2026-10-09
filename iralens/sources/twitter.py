# -*- coding: utf-8 -*-
"""Twitter/X — tweets, timelines, search.

Credential policy inherited from the capability-layer source: saved cookies
are injected into the child process environment only (never exported into
the current shell, never logged). The desktop browser bridge is preferred
when connected because it reuses the user's real session.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..errors import AuthRequiredError, SourceUnavailableError
from ..model import Artifact
from ..proc import probe_command, run_cli
from ..security import host_matches, public_message
from ._opencli import opencli_status
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_CLI_CANDIDATES = ("twitter", "xreach")


def _cli_name() -> Optional[str]:
    for name in _CLI_CANDIDATES:
        if probe_command(name, ["--version"], timeout=10, package="twitter-cli").ok:
            return name
    return None


def _child_env(context: "Context") -> Dict[str, str]:
    """Saved credentials for the child process only; os.environ untouched."""
    child: Dict[str, str] = {}
    config = context.config
    for env_name, config_key in (("TWITTER_AUTH_TOKEN", "twitter_auth_token"), ("TWITTER_CT0", "twitter_ct0")):
        if env_name in os.environ:
            continue
        value = config.get(config_key) if config else None
        if value:
            child[env_name] = str(value)
    return child


class TwitterSource(Source):
    name = "twitter"
    description = "Twitter/X tweets, timelines, search"
    backends = ["browser-bridge", "twitter-cli"]
    tier = 1
    operations = {
        "feed": {"description": "Home timeline", "params": {"limit": "default 20"}},
        "tweet": {"description": "Read one tweet (+replies)", "params": {"url": "tweet URL or id"}},
        "user_posts": {"description": "A user's timeline", "params": {"user": "@handle", "limit": "default 20"}},
        "user": {"description": "User profile", "params": {"user": "@handle"}},
        "search": {"description": "Search tweets", "params": {"query": "text", "limit": "default 10"}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "x.com", "twitter.com")

    def health(self, context: "Context") -> SourceHealth:
        st = opencli_status()
        if st.installed and st.ready:
            self.active_backend = "browser-bridge"
            return SourceHealth("ok", "desktop browser bridge connected (uses your X login)", self.active_backend)
        cli = _cli_name()
        if cli:
            env = _child_env(context)
            if env or ("TWITTER_AUTH_TOKEN" in os.environ and "TWITTER_CT0" in os.environ):
                self.active_backend = "twitter-cli"
                return SourceHealth("ok", f"{cli} with credentials configured", self.active_backend)
            self.active_backend = None
            return SourceHealth(
                "warn", f"{cli} installed but no credentials",
                hint="iralens configure twitter_auth_token / twitter_ct0 (from a cookie export)",
            )
        self.active_backend = None
        return SourceHealth(
            "off", "no X backend",
            hint="pipx install twitter-cli, or install the desktop browser bridge",
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        bridge = opencli_status()
        use_bridge = bridge.installed and bridge.ready
        cli = None if use_bridge else _cli_name()
        if not use_bridge and not cli:
            raise SourceUnavailableError(
                "no X backend available",
                hint="pipx install twitter-cli (+credentials), or install the desktop browser bridge",
            )
        if not use_bridge and not _child_env(context) and "TWITTER_AUTH_TOKEN" not in os.environ:
            raise AuthRequiredError(
                "X access needs credentials",
                hint="iralens configure twitter_auth_token=... twitter_ct0=...",
            )

        limit = str(int(params.get("limit") or (10 if op == "search" else 20)))
        env = None if use_bridge else _child_env(context)

        if use_bridge:
            mapping = {
                "feed": ["opencli", "twitter", "feed", "-f", "yaml"],
                "tweet": ["opencli", "twitter", "tweet", str(params.get("url", "")), "-f", "yaml"],
                "user_posts": ["opencli", "twitter", "user-posts", str(params.get("user", "")), "-f", "yaml"],
                "user": ["opencli", "twitter", "user", str(params.get("user", "")), "-f", "yaml"],
                "search": ["opencli", "twitter", "search", str(params.get("query", "")), "-f", "yaml"],
            }
            if op not in mapping:
                return super().fetch(op, params, context)
            if op == "tweet":
                self.require(params, "url")
            if op in ("user_posts", "user"):
                self.require(params, "user")
            if op == "search":
                self.require(params, "query")
            out = run_cli(mapping[op], timeout=90)
            method = "cli:opencli"
        else:
            assert cli  # guaranteed by the availability check above when no bridge is used
            mapping = {
                "feed": [cli, "feed", "-n", limit],
                "tweet": [cli, "tweet", str(params.get("url", "")), "--json"],
                "user_posts": [cli, "user-posts", str(params.get("user", "")), "-n", limit, "--json"],
                "user": [cli, "user", str(params.get("user", "")), "--json"],
                "search": [cli, "search", str(params.get("query", "")), "-n", limit, "--json"],
            }
            if op not in mapping:
                return super().fetch(op, params, context)
            if op == "tweet":
                self.require(params, "url")
            if op in ("user_posts", "user"):
                self.require(params, "user")
            if op == "search":
                self.require(params, "query")
            out = run_cli(mapping[op], timeout=90, env_extra=env)
            method = f"cli:{cli}"

        if not out.ok:
            err = public_message((out.stderr or out.stdout or "").strip()[:300])
            if "auth" in err.lower() or "401" in err or "403" in err:
                raise AuthRequiredError("X rejected the stored credentials", hint="refresh twitter_auth_token/twitter_ct0")
            raise SourceUnavailableError(f"X operation '{op}' failed", detail=err)

        content = out.stdout[:20000]
        found_via = f"search:{params['query']}" if op == "search" else None
        artifacts = self._try_parse(content, op, method, found_via)
        return artifacts if artifacts else Artifact(
            title=f"X {op} result", url="https://x.com", source="twitter",
            kind="post" if op != "user" else "other", content=content, retrieval_method=method,
            discovered_from=found_via,
        )

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        result = self.fetch("tweet", {"url": url}, context)
        return result[0] if isinstance(result, list) and result else result

    @staticmethod
    def _try_parse(content: str, op: str, method: str, found_via):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return None
        items = data if isinstance(data, list) else [data]
        out = []
        for i in items:
            if not isinstance(i, dict):
                continue
            out.append(Artifact(
                title=(i.get("text") or i.get("full_text") or "")[:120],
                url=i.get("url") or i.get("tweet_url") or "",
                source="twitter", kind="tweet" if op != "user" else "other",
                content=str(i.get("text") or i.get("full_text") or "")[:3000],
                metadata={k: v for k, v in i.items() if k not in ("text", "full_text", "url", "tweet_url")},
                retrieval_method=method, discovered_from=found_via,
            ))
        return out or None
