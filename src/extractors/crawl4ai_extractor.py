"""Crawl4AI extractor — Playwright-based JS rendering with built-in stealth.

Used as the final fallback when both httpx and Jina Reader return thin content
(Cloudflare-hardened sites, heavy SPAs that block cloud renderers).

Crawl4AI bundles patchright (stealth Playwright fork) automatically.

Oracle Cloud deployment note:
  Needs one-time setup: `playwright install-deps chromium && playwright install chromium`
  On Oracle Linux ARM: sudo playwright install-deps chromium
  Disable via ENABLE_CRAWL4AI=false in .env if RAM is tight (each browser ~200MB).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_via_crawl4ai(url: str) -> Optional[str]:
    """Fetch *url* via Crawl4AI's headless browser and return clean Markdown.

    Returns None on failure, if crawl4ai is not installed, or empty result.
    Import is deferred so the module loads even without crawl4ai installed.
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    except ImportError:
        logger.warning("crawl4ai not installed — skipping Crawl4AI fallback")
        return None

    try:
        browser_cfg = BrowserConfig(headless=True, verbose=False)
        run_cfg = CrawlerRunConfig(
            page_timeout=60000,
            wait_for="body",
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)

        content = ""
        if hasattr(result, "markdown") and result.markdown:
            content = result.markdown
        elif hasattr(result, "cleaned_html") and result.cleaned_html:
            content = result.cleaned_html

        content = content.strip()
        if len(content) < 100:
            logger.debug("Crawl4AI returned near-empty response for %s", url)
            return None

        logger.info("Crawl4AI: %d chars fetched for %s", len(content), url)
        return content

    except Exception as exc:
        logger.warning("Crawl4AI failed for %s: %s", url, exc)
        return None
