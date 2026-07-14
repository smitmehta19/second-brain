"""Extractor registry — single entry point for content extraction.

Usage::

    from src.extractors import extract_content

    result: ExtractedContent = await extract_content(raw_capture)
"""

from __future__ import annotations

import logging

from src.extractors.base import BaseExtractor
from src.extractors.instagram import InstagramExtractor
from src.extractors.media import MediaExtractor
from src.extractors.substack import SubstackExtractor
from src.extractors.text import TextExtractor
from src.extractors.url_detector import (
    classify_url,
    classify_url_content_type,
    clean_url,
    detect_urls,
    get_extractor_for_url,
)
from src.extractors.web import WebExtractor
from src.extractors.youtube import YouTubeExtractor
from src.models.schemas import (
    ContentType,
    ExtractedContent,
    RawCapture,
    SourcePlatform,
)

logger = logging.getLogger(__name__)

__all__ = [
    "extract_content",
    "BaseExtractor",
    "WebExtractor",
    "YouTubeExtractor",
    "InstagramExtractor",
    "SubstackExtractor",
    "TextExtractor",
    "MediaExtractor",
    "classify_url",
    "classify_url_content_type",
    "clean_url",
    "detect_urls",
]


async def extract_content(capture: RawCapture) -> ExtractedContent:
    """Detect the content type, pick the right extractor, and run it.

    Routing chain:
      1. If the capture has a URL, resolve shortened links, classify the
         platform, and dispatch to the platform-specific extractor.
      2. If the URL extractor fails or returns nothing useful, fall back
         to the generic web extractor.
      3. If the capture is plain text (no URL), use the text extractor.
      4. If the capture is an image / voice / video, use the media extractor.
      5. As a last resort, return a minimal ``ExtractedContent``.
    """
    try:
        # ---- URL-based content -------------------------------------------
        url = capture.url or _extract_url_from_text(capture)
        if url:
            # Ensure the capture URL field is populated.
            capture = capture.model_copy(update={"url": url})
            return await _extract_url_content(capture)

        # ---- Media -------------------------------------------------------
        if capture.content_type in {ContentType.IMAGE, ContentType.VOICE, ContentType.VIDEO}:
            extractor = MediaExtractor()
            return await extractor.extract(capture)

        # ---- Plain text --------------------------------------------------
        if capture.content_type == ContentType.TEXT and capture.text:
            extractor = TextExtractor()
            return await extractor.extract(capture)

        # ---- Fallback ----------------------------------------------------
        logger.warning(
            "No extractor matched capture %s (type=%s)",
            capture.id,
            capture.content_type.value,
        )
        return _minimal_fallback(capture)

    except Exception:
        logger.exception("Unexpected error during extraction for capture %s", capture.id)
        return _minimal_fallback(capture)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_url_from_text(capture: RawCapture) -> str | None:
    """If the capture is TEXT but contains a URL, pull it out."""
    if capture.content_type != ContentType.TEXT or not capture.text:
        return None
    urls = detect_urls(capture.text)
    return urls[0] if urls else None


async def _extract_url_content(capture: RawCapture) -> ExtractedContent:
    """Route a URL capture through platform-specific then generic extractors."""
    url = capture.url or ""

    # Try platform-specific extractor.
    try:
        extractor, resolved_url = await get_extractor_for_url(url)
        capture = capture.model_copy(update={"url": resolved_url})
        result = await extractor.extract(capture)
        if result.content and result.content.strip():
            # Attach fine-grained URL content type for AI prompt selection
            url_type = classify_url_content_type(resolved_url)
            result = result.model_copy(update={
                "url": resolved_url,
                "url_content_type": url_type,
            })
            return result
    except Exception:
        logger.warning("Platform extractor failed for %s, trying web fallback", url, exc_info=True)

    # Fallback to generic web extractor.
    try:
        web = WebExtractor()
        result = await web.extract(capture)
        if result.content and result.content.strip():
            cleaned = clean_url(url)
            url_type = classify_url_content_type(cleaned)
            result = result.model_copy(update={
                "url": cleaned,
                "url_content_type": url_type,
            })
            return result
    except Exception:
        logger.warning("Web extractor fallback also failed for %s", url, exc_info=True)

    return _minimal_fallback(capture)


def _minimal_fallback(capture: RawCapture) -> ExtractedContent:
    """Absolute last-resort content object so we never return None."""
    return ExtractedContent(
        raw_id=capture.id,
        title=capture.url or "Untitled Capture",
        content=capture.text or capture.url or "No content extracted.",
        url=capture.url,
        source_platform=SourcePlatform.UNKNOWN,
        content_type=capture.content_type,
    )
