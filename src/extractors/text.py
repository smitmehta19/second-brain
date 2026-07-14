"""Plain text processor for raw text captures (thoughts, notes, copied text)."""

from __future__ import annotations

import logging
import re

from src.extractors.base import BaseExtractor
from src.models.schemas import (
    ContentType,
    ExtractedContent,
    RawCapture,
    SourcePlatform,
)

logger = logging.getLogger(__name__)

# Heuristic thresholds
_THOUGHT_MAX_LEN = 280  # Short texts are likely personal thoughts
_COPIED_INDICATORS = [
    "http",
    "www.",
    "according to",
    "source:",
    "via ",
    "published",
    '"',  # Quoted text suggests copied content
]


class TextExtractor(BaseExtractor):
    """Process plain text captures — thoughts, ideas, and copied content."""

    name = "text"

    async def can_handle(self, capture: RawCapture) -> bool:
        return capture.content_type == ContentType.TEXT and bool(capture.text)

    async def extract(self, capture: RawCapture) -> ExtractedContent:
        raw_text = capture.text or ""
        try:
            cleaned = self._clean_text(raw_text)
            is_thought = self._is_thought(cleaned)

            title = self._generate_title(cleaned, is_thought)

            metadata: dict = {
                "is_thought": is_thought,
                "original_length": len(raw_text),
            }

            return ExtractedContent(
                raw_id=capture.id,
                title=title,
                content=cleaned,
                source_platform=SourcePlatform.THOUGHT,
                content_type=ContentType.TEXT,
                metadata=metadata,
            )
        except Exception:
            logger.exception("Text extraction failed")
            return self._fallback_content(
                capture,
                title="Text Note",
                content=raw_text,
                source_platform=SourcePlatform.THOUGHT,
            )

    # ------------------------------------------------------------------
    # Cleaning
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean up WhatsApp-style formatting and common noise."""
        # Remove WhatsApp forward header.
        text = re.sub(r"^\*Forwarded\*\n?", "", text, flags=re.MULTILINE)

        # Replace WhatsApp bold (*text*) with markdown bold.
        text = re.sub(r"\*([^*\n]+)\*", r"**\1**", text)

        # Replace WhatsApp italic (_text_) with markdown italic.
        text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"*\1*", text)

        # Replace WhatsApp strikethrough (~text~) with markdown.
        text = re.sub(r"~([^~\n]+)~", r"~~\1~~", text)

        # Replace WhatsApp monospace (```text```) — already markdown.
        # No change needed.

        # Collapse excessive blank lines.
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Strip leading/trailing whitespace.
        text = text.strip()

        return text

    @staticmethod
    def _is_thought(text: str) -> bool:
        """Heuristic: short text without external-content markers is a thought."""
        if len(text) > _THOUGHT_MAX_LEN:
            return False

        text_lower = text.lower()
        for indicator in _COPIED_INDICATORS:
            if indicator in text_lower:
                return False

        # If it looks like a single sentence / fragment, it is a thought.
        sentence_count = len(re.findall(r"[.!?]\s", text)) + 1
        return sentence_count <= 3

    def _generate_title(self, text: str, is_thought: bool) -> str:
        """Create a concise title from the text content."""
        if is_thought:
            return self._safe_truncate(text, 80) or "Quick Thought"

        # For longer / copied content, use the first line or sentence.
        first_line = text.split("\n", 1)[0].strip()
        first_sentence = re.split(r"[.!?]\s", first_line, maxsplit=1)[0]
        title = self._safe_truncate(first_sentence, 80)
        return title or "Text Note"
