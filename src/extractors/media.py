"""Image and voice media handler.

Saves media files to the vault's _Attachments folder and creates
placeholder notes.  Actual transcription / OCR is deferred to the AI
categorizer pipeline.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from src.extractors.base import BaseExtractor
from src.models.schemas import (
    ContentType,
    ExtractedContent,
    RawCapture,
    SourcePlatform,
)

logger = logging.getLogger(__name__)

_MEDIA_TYPES = {ContentType.IMAGE, ContentType.VOICE, ContentType.VIDEO}


class MediaExtractor(BaseExtractor):
    """Handle image, voice, and video file captures."""

    name = "media"

    def __init__(self, vault_path: Optional[str] = None) -> None:
        self._vault_path = vault_path

    async def can_handle(self, capture: RawCapture) -> bool:
        return capture.content_type in _MEDIA_TYPES

    async def extract(self, capture: RawCapture) -> ExtractedContent:
        try:
            if capture.content_type == ContentType.IMAGE:
                return await self._handle_image(capture)
            if capture.content_type == ContentType.VOICE:
                return await self._handle_voice(capture)
            if capture.content_type == ContentType.VIDEO:
                return await self._handle_video(capture)
            return self._fallback_content(capture, title="Media File")
        except Exception:
            logger.exception(
                "Media extraction failed for %s (type=%s)",
                capture.file_path or capture.file_id,
                capture.content_type.value,
            )
            return self._fallback_content(
                capture,
                title=f"{capture.content_type.value.title()} Capture",
                content=capture.caption or "Media file received.",
            )

    # ------------------------------------------------------------------
    # Image
    # ------------------------------------------------------------------

    async def _handle_image(self, capture: RawCapture) -> ExtractedContent:
        saved_path = self._save_to_attachments(capture, ext=".jpg")
        caption = capture.caption or ""

        parts: list[str] = []
        parts.append("# Image Capture\n")
        if saved_path:
            # Obsidian-style embed.
            parts.append(f"![[{Path(saved_path).name}]]\n")
        if caption:
            parts.append(f"**Caption:** {caption}\n")
        parts.append("*Pending OCR / analysis by AI categorizer.*")

        content = "\n".join(parts)
        title = self._safe_truncate(caption, 60) or "Image Capture"

        images = [saved_path] if saved_path else []

        return ExtractedContent(
            raw_id=capture.id,
            title=title,
            content=content,
            source_platform=SourcePlatform.TELEGRAM,
            content_type=ContentType.IMAGE,
            images=images,
            metadata={"needs_ocr": True, "saved_path": saved_path or ""},
        )

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------

    async def _handle_voice(self, capture: RawCapture) -> ExtractedContent:
        saved_path = self._save_to_attachments(capture, ext=".ogg")
        caption = capture.caption or ""

        parts: list[str] = []
        parts.append("# Voice Note\n")
        if saved_path:
            parts.append(f"**Audio file:** [[{Path(saved_path).name}]]\n")
        if caption:
            parts.append(f"**Caption:** {caption}\n")
        parts.append("*Pending transcription by AI categorizer.*")

        content = "\n".join(parts)

        return ExtractedContent(
            raw_id=capture.id,
            title=caption or "Voice Note",
            content=content,
            source_platform=SourcePlatform.TELEGRAM,
            content_type=ContentType.VOICE,
            metadata={
                "needs_transcription": True,
                "saved_path": saved_path or "",
            },
        )

    # ------------------------------------------------------------------
    # Video
    # ------------------------------------------------------------------

    async def _handle_video(self, capture: RawCapture) -> ExtractedContent:
        saved_path = self._save_to_attachments(capture, ext=".mp4")
        caption = capture.caption or ""

        parts: list[str] = []
        parts.append("# Video Capture\n")
        if saved_path:
            parts.append(f"**Video file:** [[{Path(saved_path).name}]]\n")
        if caption:
            parts.append(f"**Caption:** {caption}\n")

        content = "\n".join(parts)

        return ExtractedContent(
            raw_id=capture.id,
            title=caption or "Video Capture",
            content=content,
            source_platform=SourcePlatform.TELEGRAM,
            content_type=ContentType.VIDEO,
            metadata={"saved_path": saved_path or ""},
        )

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _save_to_attachments(
        self,
        capture: RawCapture,
        *,
        ext: str = "",
    ) -> Optional[str]:
        """Copy the source file into the vault's _Attachments folder.

        Returns the path of the saved file, or None if no source file
        is available or the vault path is not configured.
        """
        source = capture.file_path
        if not source or not Path(source).exists():
            logger.debug("No local file to save for capture %s", capture.id)
            return None

        vault = self._resolve_vault_path()
        if vault is None:
            return None

        attachments_dir = vault / "_Attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        # Generate a unique filename.
        if not ext:
            ext = Path(source).suffix or ""
        filename = f"{capture.id}_{uuid.uuid4().hex[:6]}{ext}"
        dest = attachments_dir / filename

        try:
            shutil.copy2(source, dest)
            logger.info("Saved media to %s", dest)
            return str(dest)
        except Exception:
            logger.warning("Could not copy %s to attachments", source, exc_info=True)
            return None

    def _resolve_vault_path(self) -> Optional[Path]:
        """Return the vault Path, trying the constructor arg then settings."""
        if self._vault_path:
            return Path(self._vault_path)
        try:
            from src.config.settings import get_settings
            return get_settings().vault_path
        except Exception:
            logger.debug("Could not resolve vault path from settings")
            return None
