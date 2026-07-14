"""Generic web article extractor.

Uses trafilatura as the primary extraction engine with beautifulsoup4 +
readability-lxml as a fallback.  HTML is converted to clean Markdown via
markdownify.  Also extracts JSON-LD structured data (Schema.org) when
present — gives recipes, products, articles etc. directly.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

import httpx
import trafilatura
from markdownify import markdownify as md

from src.extractors.base import BaseExtractor
from src.models.schemas import (
    ContentType,
    ExtractedContent,
    RawCapture,
    SourcePlatform,
)

logger = logging.getLogger(__name__)


class WebExtractor(BaseExtractor):
    """Extract clean article content from any web page."""

    name = "web"

    async def can_handle(self, capture: RawCapture) -> bool:
        return capture.content_type == ContentType.URL and bool(capture.url)

    async def extract(self, capture: RawCapture) -> ExtractedContent:
        url = capture.url or ""
        try:
            html: Optional[str] = None  # populated only on trafilatura fallback path
            result = await self._extract_with_jina_primary(url)

            if result is None:
                # Jina quota exhausted or failed — fall back to httpx + trafilatura
                logger.info("Jina unavailable — falling back to trafilatura for %s", url)
                html = await self._fetch_html(url)
                if html:
                    result = self._extract_with_trafilatura(html, url)
                    if result is None:
                        result = self._extract_with_readability(html, url)
                    # Last resort: Crawl4AI (local Playwright)
                    if result is None or is_thin_content(result.get("content", "")):
                        crawl = await self._try_crawl4ai(url)
                        if crawl:
                            title = self._title_from_enhanced(crawl) or self._title_from_html(html) or url
                            result = {"title": title, "content": crawl, "author": None, "date": None, "description": None}

            if result is None:
                return self._fallback_content(
                    capture,
                    title=url,
                    content=f"Could not fetch content from {url}",
                    source_platform=SourcePlatform.WEB,
                )

            # Extract JSON-LD structured data — only available when httpx fetched raw HTML
            jsonld = self._extract_jsonld(html) if html else []
            if jsonld:
                structured_text = self._jsonld_to_text(jsonld)
                if structured_text:
                    result["content"] = (
                        f"=== STRUCTURED DATA FROM PAGE ===\n{structured_text}\n"
                        f"=== END STRUCTURED DATA ===\n\n{result['content']}"
                    )

            # SerpApi product data fallback — called only when needed to preserve quota:
            # - Amazon URLs: always (Amazon never publishes JSON-LD price data)
            # - Other ecommerce: only when no price found in already-extracted content
            serpapi_text = await self._fetch_serpapi_product(url, result)
            if serpapi_text:
                result["content"] = serpapi_text + "\n\n" + result["content"]

            # Image fallback: try og:image from raw HTML if we don't have any
            images = result.get("images") or []
            if not images and html:
                og_img = self._og_image_from_html(html)
                if og_img:
                    images = [og_img]

            return ExtractedContent(
                raw_id=capture.id,
                title=result["title"] or url,
                content=result["content"],
                summary=self._safe_truncate(result.get("description"), 500),
                url=url,
                author=result.get("author"),
                source_platform=SourcePlatform.WEB,
                content_type=ContentType.URL,
                images=images,
                metadata={
                    k: v
                    for k, v in {
                        "publish_date": result.get("date"),
                        "description": result.get("description"),
                    }.items()
                    if v
                },
            )
        except Exception:
            logger.exception("Web extraction failed for %s", url)
            return self._fallback_content(
                capture,
                title=url,
                content=f"Extraction error for {url}",
                source_platform=SourcePlatform.WEB,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _og_image_from_html(html: str) -> Optional[str]:
        """Extract og:image meta tag as a product image fallback."""
        import re as _re
        m = _re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, _re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            if url.startswith("http"):
                return url
        return None

    @staticmethod
    def _title_from_html(html: str) -> Optional[str]:
        """Extract page title from raw HTML via og:title or <title> tag."""
        import re as _re
        og = _re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, _re.IGNORECASE)
        if og:
            return og.group(1).strip()
        title = _re.search(r'<title[^>]*>(.*?)</title>', html, _re.IGNORECASE | _re.DOTALL)
        if title:
            return title.group(1).strip()
        return None

    @staticmethod
    def _title_from_enhanced(content: str) -> Optional[str]:
        """Extract product title from Jina Reader's 'Title: ...' header line."""
        import re as _re
        m = _re.search(r'^Title:\s*(.+)$', content, _re.MULTILINE)
        if m:
            t = m.group(1).strip()
            # Strip Jina's "Buy X Online" prefix noise when present
            t = _re.sub(r'^Buy\s+', '', t, flags=_re.IGNORECASE)
            t = _re.sub(r'\s+Online\s*$', '', t, flags=_re.IGNORECASE)
            return t.strip() or None
        # Fallback: first H1 heading
        h1 = _re.search(r'^#\s+(.+)$', content, _re.MULTILINE)
        if h1:
            return h1.group(1).strip()
        return None

    _GENERIC_TITLE_SIGNALS = (
        "online shopping", "buy online", "shop online", "official website",
        "home page", "homepage", "welcome to", "best price", "free delivery",
    )

    @classmethod
    def _is_generic_title(cls, title: str) -> bool:
        t = title.lower()
        return len(title) > 70 or any(s in t for s in cls._GENERIC_TITLE_SIGNALS)

    async def _extract_with_jina_primary(self, url: str) -> Optional[dict]:
        """Primary extraction path: Jina AI Reader.

        Returns a result dict on success, or None when Jina is unavailable
        (quota exhausted on all keys) — caller falls back to trafilatura.
        Anonymous Jina (no key) is tried last before giving up.

        Also pre-extracts structured product data from the Jina markdown so the
        AI sees price/brand/name at the top of the content (same pattern as
        JSON-LD and SerpApi), eliminating "where is the price?" guessing.
        """
        from src.extractors.jina_reader import fetch_via_jina, is_thin_content
        try:
            from src.config.settings import get_settings
            settings = get_settings()
            if not settings.enable_jina:
                return None
            api_keys = settings.all_jina_keys
        except Exception:
            api_keys = []

        content = await fetch_via_jina(url, api_keys=api_keys, anonymous_fallback=True)
        if not content:
            return None  # all keys exhausted AND anonymous failed — caller falls back

        title = self._title_from_enhanced(content) or url
        images: list[str] = []

        # Pre-extract structured product data and prepend it (only for ecommerce URLs
        # — for articles this would be noise, and for products it pulls price/brand
        # to the top of the prompt where the AI definitely sees it).
        from src.extractors.url_detector import classify_url_content_type, reclassify_with_content
        ctype = classify_url_content_type(url)
        if ctype == "unknown":
            ctype = reclassify_with_content(url, content)
        if ctype == "ecommerce":
            from src.extractors.markdown_product_parser import (
                parse_jina_product, extract_product_images, _find_product_anchor,
            )
            structured = parse_jina_product(content, url, title=title)
            anchor = _find_product_anchor(content, title)
            images = extract_product_images(content, anchor=anchor, limit=5)
            if structured:
                content = structured + "\n\n" + content

        return {
            "title": title,
            "content": content,
            "author": None,
            "date": None,
            "description": None,
            "images": images,
        }

    async def _try_crawl4ai(self, url: str) -> Optional[str]:
        """Last-resort local Playwright extraction via Crawl4AI."""
        try:
            from src.config.settings import get_settings
            if not get_settings().enable_crawl4ai:
                return None
        except Exception:
            return None
        from src.extractors.crawl4ai_extractor import fetch_via_crawl4ai
        return await fetch_via_crawl4ai(url)

    async def _fetch_serpapi_product(
        self, url: str, result: dict
    ) -> Optional[str]:
        """Call SerpApi for product data when JSON-LD won't have it.

        Only fires when SERPAPI_API_KEY is configured — silently skipped otherwise.
        Quota-conserving: Amazon always needs it; other sites only when no price found.
        """
        from src.config.settings import get_settings
        from src.extractors.serpapi_client import (
            best_product_query,
            fetch_amazon_product,
            fetch_google_shopping,
            has_price,
            is_amazon_url,
        )

        try:
            settings = get_settings()
            api_keys = settings.all_serpapi_keys
        except Exception:
            return None
        if not api_keys:
            return None

        title = result.get("title", "") or url

        if is_amazon_url(url):
            logger.info("SerpApi: Amazon URL detected, fetching product data")
            return await fetch_amazon_product(url, api_keys)

        # For non-Amazon product URLs: only call Google Shopping when the content
        # doesn't already have a price (Jina often extracts it — no need to waste quota
        # on a Google Shopping search that may return a different retailer's product).
        product_url_patterns = (
            "/product", "/dp/", "/item/", "/p/", "/buy/",
            "/shop/", "/store/", "product_id", "item_id",
        )
        if any(p in url.lower() for p in product_url_patterns):
            current_content = result.get("content", "")
            if not has_price(current_content):
                query = best_product_query(url, result.get("title", "") or url)
                logger.info("SerpApi: no price in content, fetching Google Shopping for '%s'", query[:60])
                return await fetch_google_shopping(query, api_keys)
            logger.debug("SerpApi: price already in content — skipping Google Shopping")

        return None

    async def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch raw HTML with a realistic browser User-Agent."""
        from src.extractors.url_detector import is_safe_external_url
        if not is_safe_external_url(url):
            logger.warning("Web extractor: blocked unsafe URL (SSRF guard): %s", url)
            return None

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
                return resp.text
        except Exception:
            logger.warning("Failed to fetch %s", url, exc_info=True)
            return None

    @staticmethod
    def _extract_with_trafilatura(
        html: str,
        url: str,
    ) -> Optional[dict]:
        """Primary extraction path using trafilatura."""
        try:
            result = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                include_images=False,
                include_links=True,
                output_format="txt",
                favor_recall=True,
                deduplicate=True,
            )
            if not result:
                return None

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

            return {
                "title": title,
                "content": result,
                "author": author,
                "date": date,
                "description": description,
            }
        except Exception:
            logger.debug("trafilatura extraction failed", exc_info=True)
            return None

    @staticmethod
    def _extract_jsonld(html: str) -> list[dict]:
        """Extract JSON-LD structured data from HTML."""
        results = []
        try:
            for match in re.finditer(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html,
                re.DOTALL | re.IGNORECASE,
            ):
                try:
                    data = json.loads(match.group(1).strip())
                    if isinstance(data, list):
                        results.extend(data)
                    elif isinstance(data, dict):
                        if "@graph" in data:
                            results.extend(data["@graph"])
                        else:
                            results.append(data)
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception:
            pass
        return results

    @staticmethod
    def _jsonld_to_text(items: list[dict]) -> str:
        """Convert JSON-LD items to concise text the AI can use."""
        parts: list[str] = []
        for item in items:
            schema_type = item.get("@type", "")
            if isinstance(schema_type, list):
                schema_type = schema_type[0] if schema_type else ""

            if schema_type == "Recipe":
                parts.append(f"Recipe: {item.get('name', '')}")
                if item.get("description"):
                    parts.append(f"Description: {item['description']}")
                if item.get("recipeIngredient"):
                    parts.append("Ingredients:")
                    for ing in item["recipeIngredient"]:
                        parts.append(f"  - {ing}")
                if item.get("recipeInstructions"):
                    parts.append("Method:")
                    for i, step in enumerate(item["recipeInstructions"], 1):
                        if isinstance(step, dict):
                            text = step.get("text", "")
                        else:
                            text = str(step)
                        if text:
                            parts.append(f"  {i}. {text}")
                for field in ("prepTime", "cookTime", "totalTime",
                              "recipeYield", "recipeCategory", "recipeCuisine",
                              "keywords", "nutrition"):
                    val = item.get(field)
                    if val:
                        if isinstance(val, dict):
                            val = ", ".join(f"{k}: {v}" for k, v in val.items() if not k.startswith("@"))
                        parts.append(f"{field}: {val}")
                rating = item.get("aggregateRating")
                if rating and isinstance(rating, dict):
                    parts.append(f"Rating: {rating.get('ratingValue', '?')}/5 ({rating.get('ratingCount', '?')} reviews)")

            elif schema_type == "Product":
                parts.append(f"Product: {item.get('name', '')}")
                if item.get("description"):
                    parts.append(f"Description: {item['description'][:500]}")
                if item.get("brand"):
                    brand = item["brand"]
                    parts.append(f"Brand: {brand.get('name', brand) if isinstance(brand, dict) else brand}")
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    if offers.get("price"):
                        parts.append(f"Price: {offers.get('priceCurrency', '')} {offers['price']}")
                    if offers.get("availability"):
                        parts.append(f"Availability: {offers['availability'].split('/')[-1]}")
                if item.get("aggregateRating"):
                    r = item["aggregateRating"]
                    parts.append(f"Rating: {r.get('ratingValue', '?')}/5 ({r.get('ratingCount', '?')} reviews)")
                if item.get("sku"):
                    parts.append(f"SKU: {item['sku']}")

            elif schema_type in ("Article", "NewsArticle", "BlogPosting",
                                 "TechArticle", "ScholarlyArticle"):
                parts.append(f"Article: {item.get('headline', item.get('name', ''))}")
                if item.get("description"):
                    parts.append(f"Description: {item['description'][:500]}")
                if item.get("author"):
                    author = item["author"]
                    if isinstance(author, list):
                        names = []
                        for a in author:
                            names.append(a.get("name", str(a)) if isinstance(a, dict) else str(a))
                        parts.append(f"Author: {', '.join(names)}")
                    elif isinstance(author, dict):
                        parts.append(f"Author: {author.get('name', '')}")
                    else:
                        parts.append(f"Author: {author}")
                if item.get("datePublished"):
                    parts.append(f"Published: {item['datePublished']}")
                if item.get("articleSection"):
                    parts.append(f"Section: {item['articleSection']}")
                if item.get("wordCount"):
                    parts.append(f"Word count: {item['wordCount']}")

            elif schema_type == "JobPosting":
                parts.append(f"Job: {item.get('title', '')}")
                if item.get("description"):
                    desc = item["description"]
                    if len(desc) > 1000:
                        desc = desc[:1000] + "..."
                    parts.append(f"Description: {desc}")
                if item.get("hiringOrganization"):
                    org = item["hiringOrganization"]
                    parts.append(f"Company: {org.get('name', org) if isinstance(org, dict) else org}")
                if item.get("jobLocation"):
                    loc = item["jobLocation"]
                    if isinstance(loc, dict):
                        addr = loc.get("address", {})
                        if isinstance(addr, dict):
                            parts.append(f"Location: {addr.get('addressLocality', '')} {addr.get('addressCountry', '')}")
                if item.get("baseSalary"):
                    sal = item["baseSalary"]
                    if isinstance(sal, dict):
                        val = sal.get("value", {})
                        if isinstance(val, dict):
                            parts.append(f"Salary: {val.get('minValue', '')}-{val.get('maxValue', '')} {sal.get('currency', '')}")
                if item.get("employmentType"):
                    parts.append(f"Type: {item['employmentType']}")
                if item.get("datePosted"):
                    parts.append(f"Posted: {item['datePosted']}")

            elif schema_type == "Event":
                parts.append(f"Event: {item.get('name', '')}")
                if item.get("startDate"):
                    parts.append(f"Date: {item['startDate']}")
                if item.get("location"):
                    loc = item["location"]
                    if isinstance(loc, dict):
                        parts.append(f"Location: {loc.get('name', '')} {loc.get('address', '')}")
                if item.get("offers"):
                    offers = item["offers"]
                    if isinstance(offers, dict) and offers.get("price"):
                        parts.append(f"Price: {offers.get('priceCurrency', '')} {offers['price']}")

            elif schema_type in ("Course", "EducationalOccupationalProgram"):
                parts.append(f"Course: {item.get('name', '')}")
                if item.get("description"):
                    parts.append(f"Description: {item['description'][:500]}")
                if item.get("provider"):
                    prov = item["provider"]
                    parts.append(f"Provider: {prov.get('name', prov) if isinstance(prov, dict) else prov}")

            elif schema_type == "SoftwareApplication":
                parts.append(f"App: {item.get('name', '')}")
                if item.get("operatingSystem"):
                    parts.append(f"Platform: {item['operatingSystem']}")
                if item.get("applicationCategory"):
                    parts.append(f"Category: {item['applicationCategory']}")
                offers = item.get("offers", {})
                if isinstance(offers, dict) and offers.get("price"):
                    parts.append(f"Price: {offers.get('priceCurrency', '')} {offers['price']}")

            elif schema_type in ("VideoObject", "Movie"):
                parts.append(f"Video: {item.get('name', '')}")
                if item.get("description"):
                    parts.append(f"Description: {item['description'][:500]}")
                if item.get("duration"):
                    parts.append(f"Duration: {item['duration']}")
                if item.get("uploadDate"):
                    parts.append(f"Uploaded: {item['uploadDate']}")

            elif schema_type == "FAQPage":
                parts.append("FAQ:")
                entities = item.get("mainEntity", [])
                if isinstance(entities, list):
                    for faq in entities[:15]:
                        q = faq.get("name", "")
                        a = faq.get("acceptedAnswer", {})
                        ans = a.get("text", "") if isinstance(a, dict) else ""
                        if q:
                            parts.append(f"  Q: {q}")
                            if ans:
                                parts.append(f"  A: {ans[:300]}")

            elif schema_type == "HowTo":
                parts.append(f"How-to: {item.get('name', '')}")
                if item.get("step"):
                    for i, step in enumerate(item["step"], 1):
                        if isinstance(step, dict):
                            parts.append(f"  {i}. {step.get('text', step.get('name', ''))}")
                if item.get("totalTime"):
                    parts.append(f"Total time: {item['totalTime']}")

        return "\n".join(parts)

    @staticmethod
    def _extract_with_readability(
        html: str,
        url: str,
    ) -> Optional[dict]:
        """Fallback extraction using readability-lxml + BeautifulSoup."""
        try:
            from readability import Document  # readability-lxml
            from bs4 import BeautifulSoup

            doc = Document(html, url=url)
            title = doc.short_title() or url
            readable_html = doc.summary()

            # Convert to Markdown for a cleaner note.
            content_md = md(readable_html, strip=["img", "script", "style"])
            content_md = content_md.strip()

            if not content_md:
                return None

            # Try to pull author from meta tags.
            soup = BeautifulSoup(html, "html.parser")
            author_tag = soup.find("meta", attrs={"name": "author"})
            author = author_tag["content"] if author_tag and author_tag.get("content") else None

            date_tag = (
                soup.find("meta", attrs={"property": "article:published_time"})
                or soup.find("meta", attrs={"name": "date"})
            )
            date = date_tag["content"] if date_tag and date_tag.get("content") else None

            desc_tag = soup.find("meta", attrs={"name": "description"})
            description = desc_tag["content"] if desc_tag and desc_tag.get("content") else None

            return {
                "title": title,
                "content": content_md,
                "author": author,
                "date": date,
                "description": description,
            }
        except Exception:
            logger.debug("readability fallback failed", exc_info=True)
            return None
