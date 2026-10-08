# -*- coding: utf-8 -*-
"""Audio transcription fallback (Whisper via Groq or OpenAI).

The last-resort backend for video/podcast content without subtitles
(including Xiaoyuzhou podcasts). Credential policy: keys come from config
(HIL_GROQ_KEY / HIL_OPENAI_KEY), are sent only to the configured provider,
and never logged. Auto mode uses the first configured provider and stops on
failure — it never silently sends your audio to a second vendor.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

import requests

from ..errors import AuthRequiredError, ExtractionError, PageUnavailableError, SourceUnavailableError
from ..model import Artifact
from ..proc import probe_command
from ..security import host_matches, normalize_public_http_url
from .base import Source, SourceHealth

if TYPE_CHECKING:
    from ..core import Context

_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
_MAX_UPLOAD_BYTES = 24 * 1024 * 1024
_ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".webm", ".mp4"}


class TranscribeSource(Source):
    name = "transcribe"
    description = "Audio/podcast transcription (Whisper)"
    backends = ["groq-whisper", "openai-whisper"]
    tier = 1
    operations = {
        "transcribe": {
            "description": "Transcribe a public audio/video URL or local audio file",
            "params": {"url": "public http(s) URL or local path", "provider": "auto|groq|openai"},
        }
    }

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "xiaoyuzhoufm.com")

    def health(self, context: "Context") -> SourceHealth:
        has_key = bool(context.config.get("groq_key") or context.config.get("openai_key")
                       or os.environ.get("HIL_GROQ_KEY") or os.environ.get("HIL_OPENAI_KEY"))
        has_ffmpeg = probe_command("ffmpeg", ["-version"], timeout=10, package="ffmpeg").ok
        if has_key:
            self.active_backend = "groq-whisper" if (context.config.get("groq_key") or os.environ.get("HIL_GROQ_KEY")) else "openai-whisper"
            message = "Whisper key configured" + ("" if has_ffmpeg else " (no ffmpeg — files >24 MB cannot be split)")
            return SourceHealth("ok" if has_ffmpeg else "warn", message, self.active_backend)
        self.active_backend = None
        return SourceHealth(
            "off", "no transcription key",
            hint="halfiralens configure groq_key=... (free at console.groq.com) or openai_key=...",
        )

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        if op != "transcribe":
            return super().fetch(op, params, context)
        target = params.get("url")
        if not target:
            raise ExtractionError("transcribe requires 'url' (public audio URL or local file)")

        provider = str(params.get("provider") or "auto")
        groq_key = context.config.get("groq_key") or os.environ.get("HIL_GROQ_KEY")
        openai_key = context.config.get("openai_key") or os.environ.get("HIL_OPENAI_KEY")
        if provider == "auto":
            provider = "groq" if groq_key else ("openai" if openai_key else "")
        key = groq_key if provider == "groq" else openai_key if provider == "openai" else None
        if not key:
            raise AuthRequiredError(
                "transcription needs a provider key",
                hint="halfiralens configure groq_key=... (free) or openai_key=...",
            )

        with tempfile.TemporaryDirectory() as tmp:
            audio = self._materialize(target, Path(tmp))
            if audio.stat().st_size > _MAX_UPLOAD_BYTES:
                audio = self._shrink(audio, Path(tmp))
            text = self._whisper(audio, provider, str(key))

        return Artifact(
            title=f"Transcript: {Path(str(target)).name if not str(target).startswith('http') else target}",
            url=str(target) if str(target).startswith("http") else "",
            source="transcribe", kind="transcript", content=text,
            content_format="text",
            metadata={"provider": provider, "chars": len(text)},
            retrieval_method=f"api:{provider}-whisper",
        )

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        return self.fetch("transcribe", {"url": url}, context)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _materialize(target: str, tmp: Path) -> Path:
        value = str(target)
        if value.startswith(("http://", "https://")):
            safe = normalize_public_http_url(value)
            resp = requests.get(safe, stream=True, timeout=120)
            resp.raise_for_status()
            suffix = Path(safe.split("?")[0]).suffix or ".mp3"
            if suffix.lower() not in _ALLOWED_AUDIO_EXT:
                suffix = ".mp3"
            local = tmp / f"audio{suffix}"
            with open(local, "wb") as fh:
                for chunk in resp.iter_content(1024 * 256):
                    fh.write(chunk)
            return local
        local = Path(value).expanduser()
        if not local.is_file():
            raise PageUnavailableError(f"local audio file not found: {local}")
        return local

    @staticmethod
    def _shrink(audio: Path, tmp: Path) -> Path:
        from ..proc import run_cli

        out = tmp / "audio_small.mp3"
        run_cli(
            ["ffmpeg", "-y", "-i", str(audio), "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out)],
            timeout=300,
        )
        return out

    @staticmethod
    def _whisper(audio: Path, provider: str, key: str) -> str:
        url, model = (_GROQ_URL, "whisper-large-v3-turbo") if provider == "groq" else (_OPENAI_URL, "whisper-1")
        with open(audio, "rb") as fh:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (audio.name, fh)},
                data={"model": model},
                timeout=300,
            )
        if resp.status_code in (401, 403):
            raise AuthRequiredError(f"{provider} rejected the API key")
        if resp.status_code != 200:
            raise SourceUnavailableError(
                f"transcription failed ({resp.status_code})", detail=resp.text[:300]
            )
        payload = resp.json()
        text = payload.get("text", "")
        if not text:
            raise ExtractionError("transcription returned empty text")
        return text
