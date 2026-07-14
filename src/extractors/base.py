"""Base extractor interface for all content extractors."""

from __future__ import annotations

import abc
import logging
from typing import Optional

from src.models.schemas import ContentType, ExtractedContent, RawCapture, SourcePlatform

logger = logging.getLogger(__name__)


class BaseExtractor(abc.ABC):
    """Abstract base class that every extractor must implement."""

    # Subclasses should set this for logging/debugging.
    name: str = "base"

    # Default HTTP timeout in seconds.
    default_timeout: int = 30

    @abc.abstractmethod
    async def can_handle(self, capture: RawCapture) -> bool:
        """Return True if this extractor knows how to process *capture*."""

    @abc.abstractmethod
    async def extract(self, capture: RawCapture) -> ExtractedContent:
        """
        Extract structured content from *capture*.

        Implementations must never raise — they should always return an
        ``ExtractedContent`` even if it only contains the raw URL or text.
        """

    # ------------------------------------------------------------------
    # Helpers available to every extractor
    # ------------------------------------------------------------------

    def _fallback_content(
        self,
        capture: RawCapture,
        *,
        title: str = "Untitled",
        content: str = "",
        source_platform: SourcePlatform = SourcePlatform.UNKNOWN,
    ) -> ExtractedContent:
        """Build a minimal ``ExtractedContent`` when extraction partially fails."""
        return ExtractedContent(
            raw_id=capture.id,
            title=title,
            content=content or capture.text or "",
            url=capture.url,
            source_platform=source_platform,
            content_type=capture.content_type,
        )

    def _safe_truncate(self, text: Optional[str], max_len: int = 200) -> str:
        """Truncate text safely for use in titles or summaries."""
        if not text:
            return ""
        text = text.strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rsplit(" ", 1)[0] + "..."
