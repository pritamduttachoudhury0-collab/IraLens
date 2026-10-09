# -*- coding: utf-8 -*-
"""YouTube — metadata, subtitles, search, comments via yt-dlp.

Subtitle semantics inherited from the capability-layer study: manual captions
are reliable, auto captions may repeat lines; empty captions are not proof a
video has none; a transcribe fallback exists (see the transcribe source).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, TYPE_CHECKING

from ..errors import ExtractionError, PageUnavailableError, SourceUnavailableError
from ..model import Artifact
from ..proc import probe_command, run_cli
from ..security import host_matches
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context


def _ytdlp_ok() -> bool:
    return probe_command("yt-dlp", ["--version"], timeout=15, package="yt-dlp").ok


def _dump_json(target: str, extra: List[str] | None = None, timeout: int = 90) -> Any:
    argv = ["yt-dlp", "--dump-json", "--no-warnings", *(extra or []), target]
    result = run_cli(argv, timeout=timeout)
    if not result.ok:
        detail = (result.stderr or result.stdout or "").strip()[:400]
        if "Video unavailable" in detail or "not available" in detail:
            raise PageUnavailableError("video is unavailable", detail=detail)
        raise SourceUnavailableError("yt-dlp could not process the URL", detail=detail)
    first_line = result.stdout.strip().splitlines()
    if not first_line:
        raise ExtractionError("yt-dlp returned no metadata")
    if target.startswith("ytsearch"):
        return [json.loads(line) for line in first_line]
    return json.loads(first_line[0])


def _video_artifact(meta: Dict[str, Any], retrieval_method: str = "cli:yt-dlp") -> Artifact:
    uploader = meta.get("uploader") or meta.get("channel") or ""
    return Artifact(
        title=meta.get("title", "(untitled)"),
        url=meta.get("webpage_url") or f"https://www.youtube.com/watch?v={meta.get('id', '')}",
        source="youtube",
        kind="video",
        content=(meta.get("description") or "")[:4000],
        metadata={
            "id": meta.get("id"),
            "channel": uploader,
            "duration_seconds": meta.get("duration"),
            "view_count": meta.get("view_count"),
            "like_count": meta.get("like_count"),
            "upload_date": meta.get("upload_date"),
            "subtitles": sorted((meta.get("subtitles") or {}).keys()),
            "automatic_captions_langs": sorted((meta.get("automatic_captions") or {}).keys())[:30],
        },
        retrieval_method=retrieval_method,
    )


class YouTubeSource(Source):
    name = "youtube"
    description = "YouTube videos, subtitles, search, comments"
    backends = ["yt-dlp"]
    tier = 0
    operations = {
        "video": {"description": "Video metadata", "params": {"url": "video URL or id"}},
        "subtitles": {"description": "Subtitles/captions (VTT)", "params": {"url": "video URL", "langs": "default en,zh-Hans,zh"}},
        "search": {"description": "Search videos", "params": {"query": "text", "limit": "default 5"}},
        "comments": {"description": "Top comments (best-effort)", "params": {"url": "video URL", "limit": "default 20"}},
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "youtube.com", "youtu.be")

    def health(self, context: "Context") -> SourceHealth:
        if _ytdlp_ok():
            self.active_backend = "yt-dlp"
            return SourceHealth("ok", "video downloader available", self.active_backend)
        self.active_backend = None
        return SourceHealth(
            "off", "video downloader not installed",
            hint='pip install "yt-dlp[default]"',
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if not _ytdlp_ok():
            raise SourceUnavailableError(
                "video downloader not installed", hint='pip install "yt-dlp[default]"'
            )

        if op == "video":
            self.require(params, "url")
            return _video_artifact(_dump_json(self._as_url(params["url"])))

        if op == "search":
            self.require(params, "query")
            limit = int(params.get("limit") or 5)
            items = _dump_json(f"ytsearch{limit}:{params['query']}", ["--flat-playlist"])
            return [
                Artifact(
                    title=i.get("title", ""),
                    url=i.get("url") or f"https://www.youtube.com/watch?v={i.get('id', '')}",
                    source="youtube", kind="video",
                    content=(i.get("description") or "")[:500],
                    metadata={"channel": i.get("channel") or i.get("uploader"),
                              "duration_seconds": i.get("duration"),
                              "view_count": i.get("view_count")},
                    retrieval_method="cli:yt-dlp",
                    discovered_from=f"search:{params['query']}",
                )
                for i in (items or [])[:limit]
            ]

        if op == "subtitles":
            self.require(params, "url")
            url = self._as_url(params["url"])
            langs = params.get("langs") or "en,zh-Hans,zh"
            with tempfile.TemporaryDirectory() as tmp:
                result = run_cli(
                    ["yt-dlp", "--write-sub", "--write-auto-sub", "--sub-lang", langs,
                     "--skip-download", "--no-warnings", "-o", str(Path(tmp) / "%(id)s"), url],
                    timeout=120,
                )
                vtt_files = sorted(Path(tmp).glob("*.vtt"))
                if not vtt_files:
                    raise ExtractionError(
                        "no subtitle track found for the requested languages",
                        hint="try other langs, or transcribe the audio (source 'transcribe')",
                        detail=(result.stderr or "")[:300],
                    )
                parts = []
                for vtt in vtt_files:
                    parts.append(f"--- {vtt.name} ---\n{vtt.read_text(encoding='utf-8', errors='replace')}")
                return Artifact(
                    title=f"Subtitles: {url}",
                    url=url,
                    source="youtube", kind="transcript",
                    content="\n\n".join(parts)[:200000],
                    content_format="vtt",
                    metadata={"tracks": [v.name for v in vtt_files]},
                    retrieval_method="cli:yt-dlp",
                )

        if op == "comments":
            self.require(params, "url")
            limit = int(params.get("limit") or 20)
            url = self._as_url(params["url"])
            with tempfile.TemporaryDirectory() as tmp:
                run_cli(
                    ["yt-dlp", "--write-comments", "--skip-download", "--write-info-json",
                     "--extractor-args", f"youtube:max_comments={limit}",
                     "--no-warnings", "-o", str(Path(tmp) / "%(id)s"), url],
                    timeout=180,
                )
                info_files = sorted(Path(tmp).glob("*.info.json"))
                if not info_files:
                    raise ExtractionError("could not fetch comments")
                meta = json.loads(info_files[0].read_text(encoding="utf-8"))
            comments = meta.get("comments") or []
            lines = [
                f"{c.get('author', '?')} ({c.get('like_count', 0)} likes): {(c.get('text') or '').strip()}"
                for c in comments[:limit]
            ]
            return Artifact(
                title=f"Comments: {meta.get('title', url)}",
                url=url,
                source="youtube", kind="post",
                content="\n".join(lines),
                metadata={"comment_count": meta.get("comment_count"), "returned": len(lines)},
                retrieval_method="cli:yt-dlp",
            )

        return super().fetch(op, params, context)

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        return self.fetch("video", {"url": url}, context)

    @staticmethod
    def _as_url(url_or_id: str) -> str:
        value = str(url_or_id).strip()
        if value.startswith(("http://", "https://")):
            return value
        return f"https://www.youtube.com/watch?v={value}"
