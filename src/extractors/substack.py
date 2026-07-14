"""Substack article extractor using trafilatura."""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
import trafilatura

from src.extractors.base import BaseExtractor
from src.models.schemas import (
    ContentType,
    ExtractedContent,
    RawCapture,
    SourcePlatform,
)

logger = logging.getLogger(__name__)

_SUBSTACK_RE = re.compile(
    r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)?substack\.com/",
    re.IGNORECASE,
)


class SubstackExtractor(BaseExtractor):
    """Extract articles from Substack newsletters."""

    name = "substack"

    async def can_handle(self, capture: RawCapture) -> bool:
        if capture.content_type != ContentType.URL or not capture.url:
            return False
        return bool(_SUBSTACK_RE.search(capture.url))

    async def extract(self, capture: RawCapture) -> ExtractedContent:
        url = capture.url or ""
        try:
            html = await self._fetch_html(url)
            if not html:
                return self._fallback_content(
                    capture,
                    title=f"Substack: {url}",
                    content=f"Could not fetch Substack article: {url}",
                    source_platform=SourcePlatform.SUBSTACK,
                )

            article_text = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                include_images=False,
                output_format="txt",
                favor_recall=True,
            )

            metadata = trafilatura.extract_metadata(html, default_url=url)

            title = ""
            author = None
            date = None
            description = None

            if metadata:
                title = metadata.title or ""
                author = metadata.author
                date = str(metadata.date) if metadata.date else None
                description = metadata.description

            if not title:
                title = self._title_from_url(url)

            content_body = article_text or f"Content could not be extracted from {url}"

            # Build structured content.
            parts: list[str] = []
            parts.append(f"# {title}\n")
            if author:
                parts.append(f"**Author:** {author}")
            if date:
                parts.append(f"**Published:** {date}")
            parts.append(f"**Source:** Substack")
            parts.append(f"**URL:** {url}\n")
            if description:
                parts.append(f"> {description}\n")
            parts.append("---\n")
            parts.append(content_body)

            content = "\n".join(parts)

            meta_dict: dict = {}
            if date:
                meta_dict["publish_date"] = date
            if description:
                meta_dict["description"] = description
            newsletter = self._newsletter_name(url)
            if newsletter:
                meta_dict["newsletter"] = newsletter

            return ExtractedContent(
                raw_id=capture.id,
                title=title,
                content=content,
                summary=self._safe_truncate(description, 500),
                url=url,
                author=author,
                source_platform=SourcePlatform.SUBSTACK,
                content_type=ContentType.URL,
                metadata=meta_dict,
            )
        except Exception:
            logger.exception("Substack extraction failed for %s", url)
            return self._fallback_content(
                capture,
                title=f"Substack: {url}",
                content=f"Extraction error for {url}",
                source_platform=SourcePlatform.SUBSTACK,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_html(self, url: str) -> Optional[str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(self.default_timeout),
                headers=headers,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception:
            logger.warning("Failed to fetch Substack page: %s", url, exc_info=True)
            return None

    @staticmethod
    def _newsletter_name(url: str) -> Optional[str]:
        """Extract the subdomain newsletter name (e.g. 'lenny' from lenny.substack.com)."""
        match = re.search(r"(?:https?://)?([a-zA-Z0-9-]+)\.substack\.com", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _title_from_url(url: str) -> str:
        """Derive a rough title from the URL slug when metadata is missing."""
        match = re.search(r"/p/([^/?#]+)", url)
        if match:
            slug = match.group(1)
            return slug.replace("-", " ").title()
        return url
