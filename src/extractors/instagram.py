"""Instagram reel / post extractor using Open Graph meta tags."""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from src.extractors.base import BaseExtractor
from src.models.schemas import (
    ContentType,
    ExtractedContent,
    RawCapture,
    SourcePlatform,
)

logger = logging.getLogger(__name__)

_INSTAGRAM_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/",
    re.IGNORECASE,
)


class InstagramExtractor(BaseExtractor):
    """Extract metadata from Instagram reels and posts via OG tags."""

    name = "instagram"

    async def can_handle(self, capture: RawCapture) -> bool:
        if capture.content_type != ContentType.URL or not capture.url:
            return False
        return bool(_INSTAGRAM_RE.search(capture.url))

    async def extract(self, capture: RawCapture) -> ExtractedContent:
        url = capture.url or ""
        try:
            og, jsonld = await self._fetch_page_data(url)

            title = og.get("og:title") or "Instagram Post"
            og_description = og.get("og:description") or og.get("description") or ""
            image = og.get("og:image") or ""
            og_type = og.get("og:type") or ""

            # Try to extract the username from the title or URL.
            author = self._extract_username(title, url)

            # Determine media type from URL path and OG type.
            media_type = self._detect_media_type(url, og_type)

            # Prefer JSON-LD articleBody for the full caption; fall back to OG.
            raw_caption = jsonld.get("articleBody") or og_description
            caption = self._clean_caption(raw_caption) if raw_caption else ""

            # Build readable content — full caption is the main body.
            parts: list[str] = []
            parts.append(f"# {title}\n")
            if author:
                parts.append(f"**Author:** @{author}")
            parts.append(f"**Type:** {media_type}")
            parts.append(f"**URL:** {url}\n")
            if caption:
                parts.append("## Caption\n")
                parts.append(caption)

            content = "\n".join(parts)

            metadata: dict = {"media_type": media_type}
            if image:
                metadata["thumbnail"] = image

            return ExtractedContent(
                raw_id=capture.id,
                title=title,
                content=content,
                summary=self._safe_truncate(caption, 500),
                url=url,
                author=f"@{author}" if author else None,
                source_platform=SourcePlatform.INSTAGRAM,
                content_type=ContentType.URL,
                images=[image] if image else [],
                metadata=metadata,
            )
        except Exception:
            logger.exception("Instagram extraction failed for %s", url)
            # Guaranteed fallback — never return nothing.
            return self._fallback_content(
                capture,
                title="Instagram Reel",
                content=f"Instagram content: {url}",
                source_platform=SourcePlatform.INSTAGRAM,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_page_data(self, url: str) -> tuple[dict[str, str], dict]:
        """Fetch the page HTML and return (og_dict, jsonld_dict)."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(self.default_timeout),
                headers=headers,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception:
            logger.warning("Failed to fetch Instagram page: %s", url, exc_info=True)
            return {}, {}

        return self._parse_og(html), self._parse_jsonld(html)

    @staticmethod
    def _parse_og(html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        og: dict[str, str] = {}
        for tag in soup.find_all("meta"):
            prop = tag.get("property") or tag.get("name") or ""
            content = tag.get("content") or ""
            if (prop.startswith("og:") or prop == "description") and content:
                og[prop] = content
        return og

    @staticmethod
    def _parse_jsonld(html: str) -> dict:
        """Extract schema.org JSON-LD data — often has full articleBody."""
        import json as _json

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = _json.loads(tag.string or "")
                if isinstance(data, list):
                    data = data[0]
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
        return {}

    @staticmethod
    def _clean_caption(text: str) -> str:
        """Remove Instagram boilerplate from OG description."""
        # Remove patterns like "1,234 likes, 56 comments - @username on Instagram: "
        text = re.sub(r'^[\d,]+\s+likes?,.*?on\s+Instagram:\s*["“]?', '', text, flags=re.IGNORECASE)
        # Remove trailing quote
        text = text.rstrip('"”').strip()
        return text

    @staticmethod
    def _extract_username(title: str, url: str) -> Optional[str]:
        """Try to pull a username from the OG title or URL path."""
        # OG title is often like "Username on Instagram: ..."
        match = re.match(r"^(.+?)\s+on\s+Instagram", title, re.IGNORECASE)
        if match:
            return match.group(1).strip().lstrip("@")

        # Fallback: first path segment after instagram.com
        match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url)
        if match:
            segment = match.group(1).lower()
            if segment not in {"p", "reel", "reels", "stories", "tv", "explore"}:
                return segment
        return None

    @staticmethod
    def _detect_media_type(url: str, og_type: str) -> str:
        url_lower = url.lower()
        if "/reel/" in url_lower or "/reels/" in url_lower:
            return "Reel"
        if "/stories/" in url_lower:
            return "Story"
        if og_type == "video":
            return "Video"
        return "Post"
