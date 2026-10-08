# -*- coding: utf-8 -*-
"""Facebook & Instagram — via the desktop browser bridge (user's session).

Thin passthrough sources: both platforms are login-walled, so the only
honest backend is the bridge reusing the user's own Chrome session.
"""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from ..errors import SourceUnavailableError
from ..model import Artifact
from ..proc import run_cli
from ..security import host_matches
from ._opencli import opencli_status
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context


class BridgeSiteSource(Source):
    """Shared implementation for bridge-only social sites."""

    site: str = ""
    domains: tuple = ()
    login_hint: str = ""
    site_ops: Dict[str, list] = {}

    def can_handle(self, url: str) -> bool:
        return host_matches(url, *self.domains)

    def health(self, context: "Context") -> SourceHealth:
        st = opencli_status()
        if st.installed and st.ready:
            self.active_backend = "browser-bridge"
            return SourceHealth("ok", f"desktop browser bridge connected (log in to {self.login_hint} in Chrome)", self.active_backend)
        self.active_backend = None
        return SourceHealth(
            "off", f"no {self.site} backend",
            hint="install the desktop browser bridge and log in to "
                 f"{self.login_hint} in Chrome",
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        st = opencli_status()
        if not (st.installed and st.ready):
            raise SourceUnavailableError(
                f"{self.site} needs the desktop browser bridge with a logged-in Chrome session",
                hint=st.hint,
            )
        if op not in self.site_ops:
            return super().fetch(op, params, context)
        argv = ["opencli", self.site, op]
        for arg in self.site_ops[op]:
            value = params.get(arg)
            if value:
                argv.append(str(value))
        argv += ["-f", "yaml"]
        out = run_cli(argv, timeout=90)
        if not out.ok:
            raise SourceUnavailableError(f"{self.site} '{op}' failed", detail=out.stderr[:300])
        return Artifact(
            title=f"{self.site} {op}", url=f"https://www.{self.login_hint}",
            source=self.name, kind="post", content=out.stdout[:20000],
            retrieval_method="cli:opencli",
            discovered_from=f"search:{params.get('query')}" if op == "search" and params.get("query") else None,
        )


class FacebookSource(BridgeSiteSource):
    name = "facebook"
    description = "Facebook posts, profiles, groups"
    backends = ["browser-bridge"]
    tier = 2
    site = "facebook"
    domains = ("facebook.com", "fb.com", "fb.watch")
    login_hint = "facebook.com"
    operations = {
        "search": {"description": "Search", "params": {"query": "text"}},
        "profile": {"description": "A profile", "params": {"target": "profile URL/id"}},
        "feed": {"description": "Home feed", "params": {}},
        "groups": {"description": "Your groups", "params": {}},
    }
    site_ops = {"search": ["query"], "profile": ["target"], "feed": [], "groups": []}


class InstagramSource(BridgeSiteSource):
    name = "instagram"
    description = "Instagram posts and profiles"
    backends = ["browser-bridge"]
    tier = 2
    site = "instagram"
    domains = ("instagram.com",)
    login_hint = "instagram.com"
    operations = {
        "search": {"description": "Search", "params": {"query": "text"}},
        "profile": {"description": "A profile", "params": {"target": "profile URL/username"}},
        "feed": {"description": "Home feed", "params": {}},
    }
    site_ops = {"search": ["query"], "profile": ["target"], "feed": []}
