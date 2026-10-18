# -*- coding: utf-8 -*-
"""Indie Hackers — product directory, posts and comments (no auth).

Indie Hackers used to expose a JSON API and a `/groups` endpoint. Both are
gone: `/groups` answers 410 and the old topic routes 404, so any source
built on them reports a hard failure for a platform that is plainly
reachable. This source therefore works on what the site actually serves
today:

  * ``/products`` — the product directory (public, server-rendered).
  * ``/post/<slug>`` — a post with its comments (public).
  * the home page — the newest posts.

Structured data is preferred: Indie Hackers embeds schema.org JSON-LD
(``Article`` / ``BlogPosting``) and microformats (``h-entry``) on post
pages, and those give a typed headline, body, author, date, upvote count
and comment count. The HTML fallback exists for the pages that carry no
structured data, and it says so in the artifact's metadata instead of
pretending the data was structured.

No login is required and no private API is contacted.
"""

from __future__ import annotations

import html as _html
import json
import re
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ..errors import ExtractionError, PageUnavailableError, classify_http_status
from ..model import Artifact, PostRecord
from ..security import host_matches, normalize_public_http_url, safe_urlopen
from .base import Source, SourceHealth

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Context

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 30
_MAX_BYTES = 8 * 1024 * 1024

_BASE = "https://www.indiehackers.com"
_POST_RE = re.compile(r"indiehackers\.com/post/(?:[\w-]+/)?([\w-]+?)(?:-([0-9a-f]{8,}))?/?$")
_PRODUCT_RE = re.compile(r"indiehackers\.com/product/([\w-]+)")

_JSONLD_RE = re.compile(
    r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
#: microformat classes used by Indie Hackers
_HENTRY_TITLE_RE = re.compile(
    r"class=[\"'][^\"']*\bp-name\b[^\"']*[\"'][^>]*>(.*?)</", re.IGNORECASE | re.DOTALL)
_HENTRY_AUTHOR_RE = re.compile(
    r"class=[\"'][^\"']*\bp-author\b[^\"']*[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HENTRY_DATE_RE = re.compile(
    r"class=[\"'][^\"']*\bdt-published\b[^\"']*[\"'][^>]*>(.*?)</", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_UPVOTES_RE = re.compile(r"([\d,.]+)\s*upvotes?", re.IGNORECASE)
_COMMENTS_RE = re.compile(r"([\d,.]+)\s*comments?", re.IGNORECASE)


def _strip_tags(fragment: str) -> str:
    return _html.unescape(_TAG_RE.sub(" ", fragment or "")).strip()


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _jsonld_blocks(html: str) -> List[Any]:
    """Every JSON-LD payload on the page, flattened into a list."""
    blocks: List[Any] = []
    for raw in _JSONLD_RE.findall(html or ""):
        text = _html.unescape(raw).strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            blocks.extend(data)
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                blocks.extend(graph)
            else:
                blocks.append(data)
    return blocks


def _first_article(blocks: List[Any]) -> Dict[str, Any]:
    """The first schema.org article-ish block."""
    wanted = ("article", "blogposting", "newsarticle", "discussionforumposting",
              "socialmediaposting", "webpage", "creativework")
    for block in blocks:
        if not isinstance(block, dict):
            continue
        types = block.get("@type")
        names = [types] if isinstance(types, str) else list(types or [])
        lowered = [str(n).lower() for n in names]
        if any(any(w in n for w in wanted) for n in lowered):
            return block
    for block in blocks:
        if isinstance(block, dict) and block.get("articleBody"):
            return block
    return {}


def _article_record(article: Dict[str, Any]) -> PostRecord:
    """A typed record from a schema.org article block."""
    author = article.get("author")
    if isinstance(author, dict):
        author = author.get("name")
    elif isinstance(author, list) and author:
        first = author[0]
        author = first.get("name") if isinstance(first, dict) else str(first)
    stats = article.get("interactionStatistic")
    upvotes: Optional[int] = None
    comments: Optional[int] = None
    if isinstance(stats, dict):
        stats = [stats]
    if isinstance(stats, list):
        for stat in stats:
            if not isinstance(stat, dict):
                continue
            kind = str(stat.get("interactionType") or "").lower()
            count = _as_int(stat.get("userInteractionCount"))
            if count is None:
                continue
            if "comment" in kind:
                comments = count
            elif "like" in kind or "upvote" in kind:
                upvotes = count
    published = str(article.get("datePublished") or article.get("dateCreated") or "") or None
    return PostRecord(
        title=str(article.get("headline") or article.get("name") or ""),
        url=str(article.get("url") or article.get("mainEntityOfPage") or ""),
        author=str(author or ""),
        score=upvotes,
        comments=comments,
        created_at=published,
        body=str(article.get("articleBody") or ""),
        source="indiehackers",
        extra={"structured": "json-ld"},
    )


def _microformat_record(html: str) -> Dict[str, Any]:
    """What microformats (``h-entry``) say about the post, if anything."""
    out: Dict[str, Any] = {}
    title = _HENTRY_TITLE_RE.search(html or "")
    if title:
        out["title"] = _strip_tags(title.group(1))
    author = _HENTRY_AUTHOR_RE.search(html or "")
    if author:
        out["author"] = _strip_tags(author.group(1))
    date = _HENTRY_DATE_RE.search(html or "")
    if date:
        out["created_at"] = _strip_tags(date.group(1))
    return out


def _counts_from_text(text: str) -> Dict[str, Optional[int]]:
    """Upvote and comment counts as rendered next to a post."""
    up = _UPVOTES_RE.search(text or "")
    com = _COMMENTS_RE.search(text or "")
    return {
        "score": _as_int(up.group(1)) if up else None,
        "comments": _as_int(com.group(1)) if com else None,
    }


def extract_post_records(html: str, base_url: str = _BASE) -> List[PostRecord]:
    """Every post referenced by a page, as typed records.

    The Indie Hackers home page and product pages render a list of posts
    as ``/post/<slug>-<id>`` links. The link text is the headline, the
    following text carries the author and the counts.
    """
    records: List[PostRecord] = []
    seen = set()
    for match in re.finditer(
            r'href=[\"\'](https?://(?:www\.)?indiehackers\.com/post/[^\"\'#?]+)'
            r'(?:\?post=([0-9A-Za-z]+))?[\"\'][^>]*>(.*?)</a>',
            html or "", re.IGNORECASE | re.DOTALL):
        url = urllib.parse.urljoin(base_url, match.group(1))
        if url in seen:
            continue
        seen.add(url)
        title = _strip_tags(match.group(3))
        if not title:
            continue
        # The counts and the author sit close to the link in the markup.
        window = html[match.end(): match.end() + 1200]
        counts = _counts_from_text(_strip_tags(window))
        author = ""
        author_match = re.search(
            r'href=[\"\'](?:https?://(?:www\.)?indiehackers\.com)?/([A-Za-z0-9_.-]+)[\"\'][^>]*>'
            r'\s*(?:<[^>]+>\s*)*([A-Za-z0-9_.-]{2,40})', window)
        if author_match:
            author = author_match.group(2)
        records.append(
            PostRecord(
                title=title,
                url=url,
                author=author,
                score=counts["score"],
                comments=counts["comments"],
                source="indiehackers",
                extra={"transport": "html"},
            )
        )
    return records


class IndieHackersSource(Source):
    name = "indiehackers"
    description = "Indie Hackers products, posts and comments"
    backends = ["public-html"]
    tier = 0
    operations = {
        "posts": {
            "description": "The newest posts (public home page)",
            "params": {"limit": "max posts (default 20)"},
        },
        "products": {
            "description": "The product directory",
            "params": {"limit": "max products (default 20)"},
        },
        "post": {
            "description": "One post with its content and comments",
            "params": {"url": "post URL"},
        },
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "indiehackers.com")

    def health(self, context: "Context") -> SourceHealth:
        self.active_backend = self.backends[0]
        return SourceHealth(
            "ok",
            "public pages (no auth); the legacy JSON API and /groups are gone",
            self.active_backend,
        )

    # ------------------------------------------------------------------ ops
    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op == "posts":
            return self._listing(
                _BASE, limit=int(params.get("limit") or 20), kind="post",
                found_via="indiehackers:posts",
            )
        if op == "products":
            return self._listing(
                f"{_BASE}/products", limit=int(params.get("limit") or 20), kind="product",
                found_via="indiehackers:products",
            )
        if op == "post":
            self.require(params, "url")
            return self.read_url(str(params["url"]), context)
        return super().fetch(op, params, context)

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        safe_url = normalize_public_http_url(url)
        html, status_code, final_url, content_type = self._get(safe_url)

        if _POST_RE.search(safe_url):
            artifact = self._post_artifact(html, safe_url, status_code, final_url, content_type)
            if artifact is not None:
                return artifact
        artifact = self._listing_artifact(html, safe_url, status_code, final_url, content_type,
                                          kind="page", found_via=f"indiehackers:{safe_url}")
        if artifact is not None:
            return artifact
        raise ExtractionError(
            "the Indie Hackers page carried no readable post or product data",
            detail="no json-ld, no microformat and no post links",
        )

    # ------------------------------------------------------------- internals
    @staticmethod
    def _get(url: str) -> tuple:
        try:
            with safe_urlopen(url, timeout=_TIMEOUT, headers={"User-Agent": _UA}) as resp:
                status_code = int(getattr(resp, "status", None)
                                  or (resp.getcode() if hasattr(resp, "getcode") else 200) or 200)
                final_url = str(getattr(resp, "url", None) or url)
                headers = getattr(resp, "headers", {}) or {}
                content_type = str(headers.get("Content-Type") or "")
                body = resp.read(_MAX_BYTES)
        except urllib.error.HTTPError as exc:
            raise classify_http_status(exc.code, layer="origin") from exc
        except Exception as exc:
            raise PageUnavailableError(
                "could not load the Indie Hackers page", detail=str(exc)[:200]
            ) from exc
        return body.decode("utf-8", errors="replace"), status_code, final_url, content_type

    def _post_artifact(self, html: str, url: str, status_code: Optional[int],
                       final_url: str, content_type: str) -> Optional[Artifact]:
        blocks = _jsonld_blocks(html)
        article = _first_article(blocks)
        record = _article_record(article) if article else None
        micro = _microformat_record(html)

        body = ""
        title = ""
        author = ""
        created = None
        upvotes: Optional[int] = None
        comments: Optional[int] = None
        structured = "none"

        if record is not None and (record.body or record.title):
            structured = record.extra.get("structured", "json-ld")
            body = record.body
            title = record.title
            author = record.author
            created = record.created_at
            upvotes = record.score
            comments = record.comments

        if not body:
            # No article body in the structured data: fall back to the
            # rendered text of the post region.
            body = self._post_body_text(html)
        if not title:
            title = micro.get("title", "")
        if not author:
            author = micro.get("author", "")
        if not created:
            created = micro.get("created_at")

        counts = _counts_from_text(_strip_tags(html[:200000]))
        if upvotes is None:
            upvotes = counts["score"]
        if comments is None:
            comments = counts["comments"]

        if not (title or body):
            return None

        post = PostRecord(
            title=title,
            url=url,
            permalink=url,
            author=author,
            subreddit="indiehackers",
            score=upvotes,
            comments=comments,
            created_at=created,
            body=body,
            source="indiehackers",
            extra={"structured": structured},
        )
        return Artifact(
            title=title or url,
            url=url,
            source="indiehackers",
            kind="post",
            content=body,
            content_format="markdown",
            metadata=post.to_dict(),
            retrieval_method="http:public-html",
            requested_url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type or None,
        )

    @staticmethod
    def _post_body_text(html: str) -> str:
        """The rendered body of a post, without the surrounding chrome."""
        from ..extract import html_to_markdown

        markers = ("post-content", "post__content", "richtext", "rich-text",
                   "content-body", "article-content")
        lowered = html.lower()
        best = ""
        for marker in markers:
            idx = lowered.find(marker)
            if idx < 0:
                continue
            start = html.rfind("<", 0, idx)
            chunk = html[start: start + 60000]
            text = html_to_markdown(chunk)
            if len(text) > len(best):
                best = text
        if best:
            return best
        return html_to_markdown(html)

    def _listing(self, url: str, *, limit: int, kind: str, found_via: str) -> List[Artifact]:
        html, status_code, final_url, content_type = self._get(url)
        artifact = self._listing_artifact(html, url, status_code, final_url, content_type,
                                          kind=kind, found_via=found_via)
        records = artifact.metadata.get("posts", []) if artifact else []
        artifacts: List[Artifact] = []
        for item in records[:limit]:
            artifacts.append(
                Artifact(
                    title=str(item.get("title") or "(untitled)"),
                    url=str(item.get("url") or url),
                    source="indiehackers",
                    kind=kind,
                    content=str(item.get("body") or ""),
                    content_format="markdown",
                    metadata=dict(item),
                    retrieval_method="http:public-html",
                    discovered_from=found_via,
                )
            )
        if not artifacts:
            raise ExtractionError(
                f"no {kind}s found at {url}",
                detail="the page rendered without any post links",
            )
        return artifacts

    def _listing_artifact(self, html: str, url: str, status_code: Optional[int],
                          final_url: str, content_type: str, *, kind: str,
                          found_via: str) -> Optional[Artifact]:
        records = extract_post_records(html, url)
        products = []
        for match in re.finditer(
                r'href=[\"\'](https?://(?:www\.)?indiehackers\.com/product/([\w-]+))',
                html or "", re.IGNORECASE):
            entry = {"product": match.group(2), "url": match.group(1)}
            if entry not in products:
                products.append(entry)
        if not records and not products:
            return None
        title = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL)
        if title_match:
            title = _strip_tags(title_match.group(1))
        return Artifact(
            title=title or url,
            url=url,
            source="indiehackers",
            kind="page",
            content="",
            content_format="markdown",
            metadata={
                "posts": [r.to_dict() for r in records],
                "post_count": len(records),
                "products": products,
                "product_count": len(products),
            },
            retrieval_method="http:public-html",
            discovered_from=found_via,
            requested_url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type or None,
        )
