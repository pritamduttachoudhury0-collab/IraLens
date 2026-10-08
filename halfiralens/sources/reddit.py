# -*- coding: utf-8 -*-
"""Reddit — honest multi-backend access.

Reality inherited from the capability-layer study: there is no zero-config
path. Anonymous JSON endpoints are bot-blocked and the official API needs
manual approval. Working backends ride a logged-in session (desktop browser
bridge or cookie-importing CLI). URL reads fall back to the shared browser
engine, which may still hit the auth wall — reported honestly as
authentication_required.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, TYPE_CHECKING

from ..errors import AuthRequiredError, SourceUnavailableError
from ..model import Artifact
from ..proc import probe_command, run_cli
from ..security import host_matches
from ._opencli import opencli_status
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_POST_RE = re.compile(r"reddit\.com/r/([\w]+)/comments/([\w]+)")


class RedditSource(Source):
    name = "reddit"
    description = "Reddit posts, comments, search"
    backends = ["browser-bridge", "rdt-cli", "browser"]
    tier = 2
    operations = {
        "search": {"description": "Search posts (needs logged-in backend)", "params": {"query": "text", "limit": "default 10"}},
        "post": {"description": "Read a post + comments", "params": {"url": "post URL"}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "reddit.com", "redd.it")

    def health(self, context: "Context") -> SourceHealth:
        st = opencli_status()
        if st.installed and st.ready:
            self.active_backend = "browser-bridge"
            return SourceHealth("ok", "desktop browser bridge connected (reuses your reddit login)", self.active_backend)
        if probe_command("rdt", ["--version"], timeout=10, package="rdt-cli").ok:
            self.active_backend = "rdt-cli"
            return SourceHealth("ok", "reddit CLI available (ensure it is logged in: rdt login)", self.active_backend)
        self.active_backend = None
        return SourceHealth(
            "warn",
            "no logged-in reddit backend; URL reads will try the browser engine "
            "(Reddit often requires login — anonymous APIs are blocked)",
            hint="desktop: install the browser bridge and log in to reddit.com; "
                 "server: pipx install rdt-cli && rdt login",
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op == "search":
            self.require(params, "query")
            limit = int(params.get("limit") or 10)
            st = opencli_status()
            if st.installed and st.ready:
                out = run_cli(["opencli", "reddit", "search", params["query"], "-f", "yaml"], timeout=60)
                if out.ok:
                    return self._yaml_to_artifacts(out.stdout, f"search:{params['query']}")
                raise SourceUnavailableError("reddit search via bridge failed", detail=out.stderr[:300])
            if probe_command("rdt", ["--version"], timeout=10, package="rdt-cli").ok:
                out = run_cli(["rdt", "search", params["query"], "--limit", str(limit), "--json"], timeout=60)
                if out.ok:
                    return self._json_to_artifacts(out.stdout, f"search:{params['query']}")
                raise AuthRequiredError(
                    "reddit CLI search failed — it is probably not logged in",
                    hint="run: rdt login",
                    detail=out.stderr[:300],
                )
            raise AuthRequiredError(
                "reddit search needs a logged-in backend (anonymous access is blocked by Reddit)",
                hint="install the desktop browser bridge (log in to reddit.com) or rdt-cli (rdt login)",
            )
        if op == "post":
            self.require(params, "url")
            return self.read_url(params["url"], context)
        return super().fetch(op, params, context)

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        st = opencli_status()
        if st.installed and st.ready and _POST_RE.search(url):
            out = run_cli(["opencli", "reddit", "post", url, "-f", "yaml"], timeout=60)
            if out.ok:
                artifacts = self._yaml_to_artifacts(out.stdout, url)
                if artifacts:
                    return artifacts[0]
        # Browser-engine fallback — may hit Reddit's auth wall.
        engine = context.engine()
        engine.navigate(url)
        markdown = engine.markdown(max_chars=20000)
        content = markdown if isinstance(markdown, str) else str(markdown)
        current = engine.current_url_title()
        low = content.lower()
        if "whoa there" in low or "network policy" in low or "log in" in low[:500]:
            raise AuthRequiredError(
                "Reddit served a login/bot wall to the browser",
                hint="use a logged-in backend (desktop browser bridge or rdt-cli)",
            )
        return Artifact(
            title=current.get("title") or url,
            url=current.get("url") or url,
            source="reddit", kind="post", content=content,
            content_format="markdown", retrieval_method="browser",
        )

    # ------------------------------------------------------------ parsers
    @staticmethod
    def _yaml_to_artifacts(text: str, found_via: str):
        try:
            import yaml

            data = yaml.safe_load(text)
        except Exception:
            data = None
        items = data if isinstance(data, list) else []
        return [
            Artifact(
                title=str(i.get("title", "")), url=str(i.get("url", i.get("permalink", ""))),
                source="reddit", kind="post",
                content=str(i.get("selftext") or i.get("body") or "")[:3000],
                metadata={k: v for k, v in i.items() if k not in ("title", "url", "permalink", "selftext", "body")},
                retrieval_method="cli:opencli", discovered_from=found_via,
            )
            for i in items if isinstance(i, dict)
        ]

    @staticmethod
    def _json_to_artifacts(text: str, found_via: str):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        items = data if isinstance(data, list) else data.get("results", [])
        return [
            Artifact(
                title=str(i.get("title", "")), url=str(i.get("url", i.get("permalink", ""))),
                source="reddit", kind="post",
                content=str(i.get("selftext") or "")[:3000],
                metadata={"score": i.get("score"), "comments": i.get("num_comments"),
                          "subreddit": i.get("subreddit")},
                retrieval_method="cli:rdt", discovered_from=found_via,
            )
            for i in items if isinstance(i, dict)
        ]
