"""YouTube content extractor using yt-dlp for metadata and transcripts."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from src.extractors.base import BaseExtractor
from src.models.schemas import (
    ContentType,
    ExtractedContent,
    RawCapture,
    SourcePlatform,
)

logger = logging.getLogger(__name__)

_YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


class YouTubeExtractor(BaseExtractor):
    """Extract metadata and transcripts from YouTube videos and Shorts."""

    name = "youtube"

    async def can_handle(self, capture: RawCapture) -> bool:
        if capture.content_type != ContentType.URL or not capture.url:
            return False
        return bool(_YOUTUBE_RE.search(capture.url))

    async def extract(self, capture: RawCapture) -> ExtractedContent:
        url = capture.url or ""
        try:
            info = await self._fetch_metadata(url)
            if info is None:
                return self._fallback_content(
                    capture,
                    title=f"YouTube: {url}",
                    content=f"Could not extract metadata from {url}",
                    source_platform=SourcePlatform.YOUTUBE,
                )

            title = info.get("title", url)
            description = info.get("description", "")
            channel = info.get("channel") or info.get("uploader", "Unknown")
            duration = info.get("duration")
            view_count = info.get("view_count")
            upload_date = info.get("upload_date")  # YYYYMMDD string
            thumbnail = info.get("thumbnail", "")
            is_short = self._is_short(url, info)

            transcript = await self._fetch_transcript(info)

            # Build a rich content body.
            parts: list[str] = []
            parts.append(f"# {title}\n")
            parts.append(f"**Channel:** {channel}")
            if duration is not None:
                parts.append(f"**Duration:** {self._fmt_duration(duration)}")
            if view_count is not None:
                parts.append(f"**Views:** {view_count:,}")
            if upload_date:
                parts.append(f"**Uploaded:** {self._fmt_date(upload_date)}")
            if is_short:
                parts.append("**Format:** YouTube Short")
            parts.append(f"**URL:** {url}\n")

            if description:
                parts.append("## Description\n")
                parts.append(description.strip())

            if transcript:
                parts.append("\n## Transcript\n")
                parts.append(transcript)

            content = "\n".join(parts)

            metadata: dict[str, Any] = {
                "channel": channel,
                "is_short": is_short,
            }
            if duration is not None:
                metadata["duration_seconds"] = duration
            if view_count is not None:
                metadata["view_count"] = view_count
            if upload_date:
                metadata["upload_date"] = upload_date
            if thumbnail:
                metadata["thumbnail"] = thumbnail

            return ExtractedContent(
                raw_id=capture.id,
                title=title,
                content=content,
                summary=self._safe_truncate(description, 500),
                url=url,
                author=channel,
                source_platform=SourcePlatform.YOUTUBE,
                content_type=ContentType.VIDEO,
                images=[thumbnail] if thumbnail else [],
                metadata=metadata,
            )
        except Exception:
            logger.exception("YouTube extraction failed for %s", url)
            return self._fallback_content(
                capture,
                title=f"YouTube: {url}",
                content=f"Extraction error for {url}",
                source_platform=SourcePlatform.YOUTUBE,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_metadata(self, url: str) -> Optional[dict]:
        """Use yt-dlp to extract metadata without downloading media."""
        try:
            import yt_dlp
        except ImportError:
            logger.error("yt-dlp is not installed — cannot extract YouTube metadata")
            return None

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "en-orig"],
            "socket_timeout": self.default_timeout,
        }

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _extract)

    async def _fetch_transcript(self, info: dict) -> Optional[str]:
        """Extract transcript text from yt-dlp subtitle data."""
        try:
            # yt-dlp provides subtitles / automatic_captions dicts.
            subs = info.get("subtitles") or {}
            auto_subs = info.get("automatic_captions") or {}

            # Prefer manual English subs, then auto.
            sub_list = (
                subs.get("en")
                or subs.get("en-orig")
                or auto_subs.get("en")
                or auto_subs.get("en-orig")
            )

            if not sub_list:
                return None

            # Pick a text-based format.
            sub_entry = None
            for fmt in ("srv3", "srv2", "srv1", "vtt", "ttml", "json3"):
                for s in sub_list:
                    if s.get("ext") == fmt:
                        sub_entry = s
                        break
                if sub_entry:
                    break

            if sub_entry is None:
                sub_entry = sub_list[0] if sub_list else None
            if sub_entry is None:
                return None

            sub_url = sub_entry.get("url")
            if not sub_url:
                return None

            import httpx

            async with httpx.AsyncClient(timeout=httpx.Timeout(self.default_timeout)) as client:
                resp = await client.get(sub_url)
                resp.raise_for_status()
                raw = resp.text

            return self._clean_subtitle_text(raw)
        except Exception:
            logger.debug("Transcript extraction failed", exc_info=True)
            return None

    @staticmethod
    def _clean_subtitle_text(raw: str) -> str:
        """Strip VTT/SRV timestamps and duplicates, returning plain text."""
        lines: list[str] = []
        prev = ""
        for line in raw.splitlines():
            line = line.strip()
            # Skip timestamp lines, numeric cue ids, WEBVTT header, and blanks.
            if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
                continue
            if re.match(r"^\d+$", line):
                continue
            if re.match(r"\d{2}:\d{2}", line):
                continue
            # Remove inline tags like <c> </c> <00:00:01.234>
            cleaned = re.sub(r"<[^>]+>", "", line)
            cleaned = cleaned.strip()
            if cleaned and cleaned != prev:
                lines.append(cleaned)
                prev = cleaned
        return "\n".join(lines)

    @staticmethod
    def _is_short(url: str, info: dict) -> bool:
        if "/shorts/" in url:
            return True
        duration = info.get("duration")
        if duration and duration <= 60:
            return True
        return False

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @staticmethod
    def _fmt_date(yyyymmdd: str) -> str:
        try:
            return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
        except Exception:
            return yyyymmdd
