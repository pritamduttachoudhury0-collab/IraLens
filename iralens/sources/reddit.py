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
import urllib.error
import urllib.parse
from typing import Any, Dict, List, TYPE_CHECKING

from ..errors import AuthRequiredError, ExtractionError, SourceUnavailableError, classify_http_status
from ..model import Artifact, PostRecord
from ..proc import probe_command, run_cli
from ..security import host_matches, normalize_public_http_url, safe_urlopen
from ._opencli import opencli_status
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_POST_RE = re.compile(r"reddit\.com/r/([\w]+)/comments/([\w]+)")

#: Phrases Reddit uses on its interstitial walls. The wall is served with
#: HTTP 200 (or a 403), so its *content* is the only remaining evidence.
_WALL_PHRASES = (
    "whoa there",
    "network policy",
    "you've been blocked",
    "you have been blocked",
    "reddit is blocking",
    "blocked by network policy",
    "please log in to reddit",
    "you need to log in",
    "this post is not available",
    "verify you are human",
    "unusual traffic",
)

#: A markdown image and nothing else: Reddit's wall renders a picture, so
#: "content" that is only an image is the wall, not a post.
_IMAGE_ONLY_RE = re.compile(r"^\s*!?\[[^\]]*\]\([^)]*\)\s*$")
_DATA_IMAGE_RE = re.compile(r"data:image/[a-z+]+;base64,[A-Za-z0-9+/]{64,}", re.IGNORECASE)


def _looks_like_block_page(content: str) -> bool:
    """True when the retrieved text is Reddit's wall rather than a post.

    Three independent signals, because Reddit has changed the shape of the
    wall over time: the known phrases, an image-only body, and a body with
    no real prose at all.
    """
    text = str(content or "")
    low = text.lower()
    if any(phrase in low for phrase in _WALL_PHRASES):
        return True
    if _DATA_IMAGE_RE.search(text):
        return True
    stripped = text.strip()
    if stripped and _IMAGE_ONLY_RE.match(stripped):
        return True
    # A wall with no recognizable prose: too little to be a post.
    if len(stripped) < 40 and not stripped:
        return True
    return False


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _normalize_subreddit(name: str) -> str:
    """`SaaS`, `r/SaaS`, `/r/SaaS/` and `https://.../r/SaaS` all name `SaaS`."""
    text = str(name or "").strip()
    if not text:
        return ""
    if "://" in text:
        text = urllib.parse.urlsplit(text).path
    text = text.strip("/")
    if text.lower().startswith("r/"):
        text = text[2:]
    text = text.split("/")[0]
    return re.sub(r"[^\w+]", "", text)


def _record_from_feed_entry(entry: Any, subreddit: str) -> PostRecord:
    """A typed post record from one RSS/Atom entry.

    The feed's ``/r/<sub>/comments/<id>/`` link is the permalink; the
    author is taken from the Atom ``author`` name when the feed provides
    one. A feed carries no score and no comment count, so those stay
    unknown rather than being invented.
    """
    link = str((entry.get("link") or "") if isinstance(entry, dict) else "")
    title = str((entry.get("title") or "") if isinstance(entry, dict) else "")
    author = ""
    if isinstance(entry, dict):
        raw_author = entry.get("author")
        if isinstance(raw_author, dict):
            author = str(raw_author.get("name") or "")
        elif raw_author:
            author = str(raw_author)
    body = ""
    if isinstance(entry, dict):
        content = entry.get("content")
        if isinstance(content, list) and content:
            body = str(content[0].get("value") or "")
        body = body or str(entry.get("summary") or "")
    published = ""
    if isinstance(entry, dict):
        published = str(entry.get("published") or entry.get("updated") or "")
    return PostRecord(
        title=title,
        url=link,
        permalink=link,
        author=author,
        subreddit=subreddit,
        created_at=published or None,
        body=body,
        source="reddit",
        extra={"transport": "rss"},
    )


class RedditSource(Source):
    name = "reddit"
    description = "Reddit posts, comments, search"
    backends = ["browser-bridge", "rdt-cli", "rss", "browser"]
    tier = 2
    operations = {
        "search": {"description": "Search posts (needs logged-in backend)", "params": {"query": "text", "limit": "default 10"}},
        "post": {"description": "Read a post + comments", "params": {"url": "post URL"}},
        "listing": {
            "description": "List the newest posts of a subreddit/community",
            "params": {
                "subreddit": "community name, with or without the r/ prefix",
                "limit": "max posts (default 25)",
                "after": "pagination cursor from a previous listing",
            },
        },
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
        if op == "listing":
            return self.list_subreddit(
                str(params.get("subreddit") or ""),
                context,
                limit=int(params.get("limit") or 25),
                after=str(params.get("after") or ""),
            )
        return super().fetch(op, params, context)

    # ------------------------------------------------------------- listing
    def list_subreddit(self, subreddit: str, context: "Context", *, limit: int = 25,
                       after: str = "") -> Any:
        """The newest posts of a subreddit.

        A subreddit listing used to be impossible: Reddit's anonymous JSON
        API is bot-blocked, and this source only knew how to read a single
        post URL. The unauthenticated ``/r/<sub>/.rss`` feed is the one
        transport that works without a login, so it is wired in as a
        first-class backend (and is also the fallback when no logged-in
        backend is available).
        """
        name = _normalize_subreddit(subreddit)
        if not name:
            raise ExtractionError("reddit listing requires a 'subreddit' name")
        limit = max(1, min(int(limit or 25), 100))

        st = opencli_status()
        if st.installed and st.ready:
            out = run_cli(["opencli", "reddit", "subreddit", name, "-f", "yaml"],
                          timeout=60)
            if out.ok:
                artifacts = self._yaml_to_artifacts(out.stdout, f"reddit:r/{name}")
                if artifacts:
                    return artifacts[:limit]
        if probe_command("rdt", ["--version"], timeout=10, package="rdt-cli").ok:
            out = run_cli(["rdt", "subreddit", name, "--limit", str(limit), "--json"],
                          timeout=60)
            if out.ok:
                artifacts = self._json_to_artifacts(out.stdout, f"reddit:r/{name}")
                if artifacts:
                    return artifacts[:limit]

        # The anonymous RSS transport: no login required.
        artifacts = self._listing_via_rss(name, limit=limit, after=after)
        if artifacts:
            return artifacts
        raise AuthRequiredError(
            f"could not list r/{name}: no logged-in backend and the anonymous "
            f"RSS feed returned nothing",
            hint="install the desktop browser bridge (log in to reddit.com) or rdt-cli (rdt login)",
            detail="reddit_listing_unavailable",
        )

    def _listing_via_rss(self, subreddit: str, *, limit: int = 25,
                         after: str = "") -> list:
        """The unauthenticated ``/r/<sub>/.rss`` transport."""
        import feedparser

        url = f"https://www.reddit.com/r/{urllib.parse.quote(subreddit, safe='')}/.rss"
        if after:
            url += f"?after={urllib.parse.quote(after, safe='')}"
        safe_url = normalize_public_http_url(url)
        try:
            with safe_urlopen(safe_url, timeout=30, headers={"User-Agent": _UA}) as resp:
                status = int(getattr(resp, "status", None)
                             or (resp.getcode() if hasattr(resp, "getcode") else 200) or 200)
                if status >= 400:
                    raise classify_http_status(status, layer="origin")
                body = resp.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise classify_http_status(exc.code, layer="origin") from exc
        except Exception as exc:
            raise SourceUnavailableError(
                f"could not read the r/{subreddit} RSS feed", detail=str(exc)[:200]
            ) from exc

        parsed = feedparser.parse(body)
        if parsed.bozo and not parsed.entries:
            raise ExtractionError(
                f"the r/{subreddit} RSS feed could not be parsed",
                detail=str(getattr(parsed, "bozo_exception", ""))[:200],
            )
        artifacts: list = []
        for entry in parsed.entries[:limit]:
            record = _record_from_feed_entry(entry, subreddit)
            artifacts.append(
                Artifact(
                    title=record.title or "(untitled)",
                    url=record.url,
                    source="reddit",
                    kind="post",
                    content=record.body,
                    content_format="markdown",
                    metadata=record.to_dict(),
                    retrieval_method="rss",
                    discovered_from=f"reddit:r/{subreddit}",
                )
            )
        return artifacts

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
        # The navigation result is the *check* on the read. Discarding it and
        # extracting anyway is how a 403 bot wall became 25 "successful"
        # posts: the block page was stored as if it were the article.
        nav_info = engine.navigate(url)
        nav_dict: Dict[str, Any] = nav_info if isinstance(nav_info, dict) else {}
        status_code = nav_dict.get("status_code")
        if isinstance(status_code, int) and status_code >= 400:
            raise classify_http_status(status_code, layer="origin", mode="browser")
        markdown = engine.markdown(max_chars=20000)
        content = markdown if isinstance(markdown, str) else str(markdown)
        current = engine.current_url_title()
        if _looks_like_block_page(content):
            raise AuthRequiredError(
                "Reddit served a login/bot wall to the browser",
                hint="use a logged-in backend (desktop browser bridge or rdt-cli)",
                detail="reddit_block_page",
            )
        return Artifact(
            title=current.get("title") or url,
            url=current.get("url") or url,
            source="reddit", kind="post", content=content,
            content_format="markdown", retrieval_method="browser",
            requested_url=url,
            final_url=str(nav_dict.get("final_url") or current.get("url") or url),
            status_code=status_code if isinstance(status_code, int) else None,
        )

    # ------------------------------------------------------------ parsers
    @staticmethod
    def _yaml_to_artifacts(text: str, found_via: str) -> List[Artifact]:
        try:
            import yaml

            data = yaml.safe_load(text)
        except Exception:
            data = None
        items = data if isinstance(data, list) else []
        artifacts: List[Artifact] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = PostRecord.from_mapping(item, source="reddit")
            artifacts.append(
                Artifact(
                    title=record.title,
                    url=record.url or record.permalink,
                    source="reddit", kind="post",
                    content=(record.body or "")[:3000],
                    metadata=record.to_dict(),
                    retrieval_method="cli:opencli", discovered_from=found_via,
                )
            )
        return artifacts

    @staticmethod
    def _json_to_artifacts(text: str, found_via: str) -> List[Artifact]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        items = data if isinstance(data, list) else data.get("results", [])
        artifacts: List[Artifact] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = PostRecord.from_mapping(item, source="reddit")
            artifacts.append(
                Artifact(
                    title=record.title,
                    url=record.url or record.permalink,
                    source="reddit", kind="post",
                    content=(record.body or "")[:3000],
                    metadata=record.to_dict(),
                    retrieval_method="cli:rdt", discovered_from=found_via,
                )
            )
        return artifacts
