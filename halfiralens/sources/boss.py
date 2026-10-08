# -*- coding: utf-8 -*-
"""Boss直聘 — job search via the boss CLI (strict-CDP mode).

Operational rules preserved from the capability-layer study:
  - Login is the user's action; Half IraLens never types credentials or
    solves sliders.
  - `AUTH_EXPIRED` is ground truth for "browser not logged in".
  - Anti-bot security-check pages are NOT login state; don't confuse them.
  - `ENVIRONMENT_RISK` means stop immediately — no refresh, no retry.
"""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from ..errors import AuthRequiredError, SourceUnavailableError
from ..model import Artifact
from ..proc import probe_command, run_cli
from ..security import host_matches
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_CDP_URL = "http://localhost:9222"


def _boss_ok() -> bool:
    return probe_command("boss", ["--version"], timeout=10, package="boss-agent-cli").ok


class BossSource(Source):
    name = "boss"
    description = "Boss直聘 job search"
    backends = ["boss-cli"]
    tier = 2
    operations = {
        "search": {"description": "Search jobs (uses your logged-in dedicated Chrome via CDP)", "params": {"query": "text", "city": "optional"}},
        "status": {"description": "Local session status", "params": {}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "zhipin.com")

    def health(self, context: "Context") -> SourceHealth:
        if not _boss_ok():
            self.active_backend = None
            return SourceHealth(
                "off", "boss CLI not installed",
                hint="ask for Boss直聘 setup; it installs the CLI and a dedicated "
                     "127.0.0.1-bound Chrome for you to log into manually",
            )
        self.active_backend = "boss-cli"
        return SourceHealth(
            "warn",
            "boss CLI installed — real login state is only known after a search "
            "(AUTH_EXPIRED = not logged in; security-check pages are anti-bot, not login)",
            self.active_backend,
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if not _boss_ok():
            raise SourceUnavailableError("boss CLI not installed")
        if op == "status":
            out = run_cli(["boss", "status"], timeout=30)
            return Artifact(
                title="Boss session status", url="https://www.zhipin.com",
                source="boss", kind="state", content=(out.stdout or out.stderr)[:4000],
                retrieval_method="cli:boss",
            )
        if op == "search":
            self.require(params, "query")
            argv = ["boss", "--browser-source", "existing-browser", "--cdp-url", _CDP_URL,
                    "search", params["query"]]
            if params.get("city"):
                argv += ["--city", str(params["city"])]
            out = run_cli(argv, timeout=120)
            combined = out.stdout + out.stderr
            if "AUTH_EXPIRED" in combined:
                raise AuthRequiredError(
                    "the dedicated browser is not logged in to Boss直聘",
                    hint="log in inside the dedicated Chrome window, then run: boss --cdp-url "
                         + _CDP_URL + " login --cdp",
                )
            if "ENVIRONMENT_RISK" in combined:
                raise SourceUnavailableError(
                    "Boss直聘 flagged environment risk — stopping as required (do not retry)",
                )
            if not out.ok:
                raise SourceUnavailableError("boss search failed", detail=combined.strip()[:300])
            return Artifact(
                title=f"Boss jobs: {params['query']}", url="https://www.zhipin.com",
                source="boss", kind="other", content=out.stdout[:20000],
                retrieval_method="cli:boss", discovered_from=f"search:{params['query']}",
            )
        return super().fetch(op, params, context)
