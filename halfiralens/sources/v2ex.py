# -*- coding: utf-8 -*-
"""V2EX — public JSON API (no auth).

API access hardened the same way as the capability-layer source: only the
public HTTPS JSON endpoints are ever contacted.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, TYPE_CHECKING
from urllib.parse import urlencode, urlsplit

from ..errors import ExtractionError, PageUnavailableError
from ..model import Artifact
from ..security import host_matches
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_UA = "half-iralens/0.1"
_TIMEOUT = 15
_MAX_RESPONSE_BYTES = 1024 * 1024
_API_BASE = "https://www.v2ex.com"
_TOPIC_RE = re.compile(r"v2ex\.com/t/(\d+)")


def _api_get(path: str, **params: Any) -> Any:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{_API_BASE}{path}" + (f"?{query}" if query else "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in ("v2ex.com", "www.v2ex.com"):
        raise ExtractionError("refusing non-V2EX API target")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read(_MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise PageUnavailableError("V2EX API request failed", detail=str(exc)) from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ExtractionError("V2EX response exceeded 1 MB cap")
    return json.loads(body.decode("utf-8"))


def _topic_artifact(topic: Dict[str, Any]) -> Artifact:
    member = (topic.get("member") or {}).get("username", "")
    node = (topic.get("node") or {}).get("title", "")
    content = topic.get("content") or ""
    return Artifact(
        title=topic.get("title", "(untitled)"),
        url=topic.get("url", ""),
        source="v2ex",
        kind="topic",
        content=content,
        content_format="text",
        metadata={
            "id": topic.get("id"),
            "member": member,
            "node": node,
            "replies": topic.get("replies"),
            "created": topic.get("created"),
        },
        retrieval_method="api:v2ex",
    )


class V2EXSource(Source):
    name = "v2ex"
    description = "V2EX topics and replies"
    backends = ["public-api"]
    tier = 0
    operations = {
        "hot": {"description": "Hot topics", "params": {}},
        "latest": {"description": "Latest topics", "params": {}},
        "topic": {"description": "One topic with content", "params": {"id": "topic id"}},
        "replies": {"description": "Replies of a topic", "params": {"id": "topic id"}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "v2ex.com")

    def health(self, context: "Context") -> SourceHealth:
        self.active_backend = self.backends[0]
        return SourceHealth("ok", "public JSON API, no auth", self.active_backend)

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op in ("hot", "latest"):
            data = _api_get(f"/api/topics/{op}.json")
            return [_topic_artifact(t) for t in data[:20]]
        if op == "topic":
            self.require(params, "id")
            data = _api_get("/api/topics/show.json", id=params["id"])
            if not data:
                raise PageUnavailableError(f"V2EX topic {params['id']} not found")
            return _topic_artifact(data[0])
        if op == "replies":
            self.require(params, "id")
            data = _api_get("/api/replies/show.json", topic_id=params["id"])
            lines = [
                f"{r.get('member', {}).get('username', '?')}: {(r.get('content') or '').strip()}"
                for r in data
            ]
            return Artifact(
                title=f"Replies of topic {params['id']}",
                url=f"https://www.v2ex.com/t/{params['id']}",
                source="v2ex",
                kind="post",
                content="\n".join(lines),
                metadata={"reply_count": len(data)},
                retrieval_method="api:v2ex",
            )
        return super().fetch(op, params, context)

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        match = _TOPIC_RE.search(url)
        if not match:
            raise ExtractionError("only v2ex.com/t/<id> topic URLs can be read natively")
        return self.fetch("topic", {"id": match.group(1)}, context)
