# -*- coding: utf-8 -*-
"""LinkedIn — profiles, companies, jobs.

Backends: the LinkedIn MCP server via the MCP bridge CLI (needs a one-time
user login), with public-page reading through the generic web reader as a
fallback for public profiles.
"""

from __future__ import annotations

import json
from typing import Any, Dict, TYPE_CHECKING

from ..errors import AuthRequiredError, SourceUnavailableError
from ..model import Artifact
from ..proc import run_cli_json
from ..security import host_matches
from shutil import which as shutil_which
from ._mcporter import mcporter_server_names
from .base import Source, SourceHealth
from .web import read_with_static_reader

if TYPE_CHECKING:
    from ..core import Context


def _mcp_ready() -> bool:
    return bool(shutil_which("mcporter")) and "linkedin" in mcporter_server_names()


class LinkedInSource(Source):
    name = "linkedin"
    description = "LinkedIn profiles, companies, jobs"
    backends = ["linkedin-mcp", "static-reader"]
    tier = 2
    operations = {
        "person": {"description": "Person profile", "params": {"username": "linkedin username", "sections": "experience,education"}},
        "people_search": {"description": "Search people", "params": {"keywords": "text", "location": "optional"}},
        "company": {"description": "Company profile", "params": {"company_name": "name", "sections": "posts,jobs"}},
        "jobs": {"description": "Search jobs", "params": {"keywords": "text", "location": "optional", "max_pages": "default 1"}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "linkedin.com")

    def health(self, context: "Context") -> SourceHealth:
        if _mcp_ready():
            self.active_backend = "linkedin-mcp"
            return SourceHealth(
                "warn", "LinkedIn MCP server configured — login validity checked at call time",
                self.active_backend,
            )
        self.active_backend = "static-reader"
        return SourceHealth(
            "warn",
            "only public-page reading available; configure the LinkedIn MCP server for full access "
            "(uvx mcp-server-linkedin@latest --login, then add it to the MCP bridge)",
            self.active_backend,
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if not _mcp_ready():
            raise SourceUnavailableError(
                "LinkedIn MCP server not configured",
                hint="uvx mcp-server-linkedin@latest --login, then register it with the MCP bridge",
            )
        if op == "person":
            self.require(params, "username")
            expr = (f'linkedin.get_person_profile(linkedin_username: {json.dumps(params["username"])}, '
                    f'sections: {json.dumps(params.get("sections", "experience,education"))})')
        elif op == "people_search":
            self.require(params, "keywords")
            expr = (f'linkedin.search_people(keywords: {json.dumps(params["keywords"])}, '
                    f'location: {json.dumps(params.get("location", ""))})')
        elif op == "company":
            self.require(params, "company_name")
            expr = (f'linkedin.get_company_profile(company_name: {json.dumps(params["company_name"])}, '
                    f'sections: {json.dumps(params.get("sections", "posts,jobs"))})')
        elif op == "jobs":
            self.require(params, "keywords")
            expr = (f'linkedin.search_jobs(keywords: {json.dumps(params["keywords"])}, '
                    f'location: {json.dumps(params.get("location", ""))}, '
                    f'max_pages: {int(params.get("max_pages") or 1)})')
        else:
            return super().fetch(op, params, context)

        try:
            payload = run_cli_json(["mcporter", "call", expr, "--timeout", "120000"], timeout=150)
        except SourceUnavailableError as exc:
            if "login" in (exc.detail or "").lower() or "auth" in (exc.detail or "").lower():
                raise AuthRequiredError(
                    "LinkedIn session expired or missing",
                    hint="uvx mcp-server-linkedin@latest --login",
                ) from exc
            raise
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return Artifact(
            title=f"LinkedIn {op}", url="https://www.linkedin.com",
            source="linkedin", kind="other", content=text[:30000],
            content_format="json", retrieval_method="mcp:linkedin",
        )

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        try:
            markdown = read_with_static_reader(url)
        except Exception as exc:
            raise AuthRequiredError(
                "LinkedIn blocks anonymous reads for this page",
                hint="configure the LinkedIn MCP server (see iralens sources)",
                detail=str(exc),
            ) from exc
        return Artifact(
            title=url, url=url, source="linkedin", kind="page",
            content=markdown, content_format="markdown", retrieval_method="static-reader",
        )
