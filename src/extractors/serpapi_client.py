"""SerpApi product data client.

Called as a fallback from WebExtractor when:
- URL is Amazon (never has JSON-LD) → amazon_product engine
- URL is other ecommerce and no price found in page → google_shopping engine

Uses httpx directly (no SDK dependency) — consistent with the rest of the project.
Free tier: 100 searches/month at serpapi.com.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_SERPAPI_BASE = "https://serpapi.com/search"

_AMAZON_PATTERN = re.compile(
    r'amazon\.(com|co\.uk|in|de|fr|ca|com\.au|co\.jp|es|it|nl|pl|se|sg)',
    re.IGNORECASE,
)
_ASIN_PATTERN = re.compile(r'/(?:dp|gp/product)/([A-Z0-9]{10})', re.IGNORECASE)
_PRICE_PATTERN = re.compile(r'(?:£|\$|€|₹|USD|GBP|EUR|INR)\s*[\d,]+(?:\.\d{2})?')

_AMAZON_COUNTRY_MAP = {
    "co.uk": "gb", "in": "in", "de": "de", "fr": "fr", "ca": "ca",
    "com.au": "au", "co.jp": "jp", "es": "es", "it": "it",
    "nl": "nl", "pl": "pl", "se": "se", "sg": "sg", "com": "us",
}


def is_amazon_url(url: str) -> bool:
    return bool(_AMAZON_PATTERN.search(url))


def has_price(text: str) -> bool:
    """Quick check: does the extracted text already contain a price?"""
    return bool(_PRICE_PATTERN.search(text))


_GENERIC_TITLE_SIGNALS = (
    "online shopping", "buy online", "shop online", "official website",
    "home page", "homepage", "welcome to", "best price", "free shipping",
)

_PRODUCT_SLUG_PATTERN = re.compile(
    r'/(?:product[s]?|item[s]?|p|dp|buy|shop)/([a-z0-9][a-z0-9\-]+[a-z0-9])',
    re.IGNORECASE,
)


def best_product_query(url: str, page_title: str) -> str:
    """Return the best search query for a product URL.

    Uses the page title when it looks product-specific; falls back to the
    URL slug when the title is a generic site header (common on JS-rendered SPAs).
    """
    title_lower = (page_title or "").lower()
    is_generic = (
        not page_title
        or len(page_title) > 80  # suspiciously long = site-wide title
        or any(sig in title_lower for sig in _GENERIC_TITLE_SIGNALS)
    )

    if not is_generic:
        return page_title

    # Extract product slug from URL path
    slug_match = _PRODUCT_SLUG_PATTERN.search(url)
    if slug_match:
        slug = slug_match.group(1)
        # Convert kebab-case to readable title, strip trailing numeric IDs
        words = [w for w in slug.split("-") if w and not (w.isdigit() and len(w) <= 2)]
        if len(words) >= 2:
            return " ".join(w.capitalize() for w in words)

    return page_title or url


def _extract_asin(url: str) -> Optional[str]:
    match = _ASIN_PATTERN.search(url)
    return match.group(1).upper() if match else None


def _amazon_country(url: str) -> str:
    m = _AMAZON_PATTERN.search(url)
    if m:
        return _AMAZON_COUNTRY_MAP.get(m.group(1).lower(), "us")
    return "us"


def _is_quota_exhausted(data: dict) -> bool:
    """SerpApi returns HTTP 200 with an error field when quota is gone."""
    error = data.get("error", "")
    return isinstance(error, str) and (
        "run out of searches" in error.lower()
        or "credits" in error.lower()
        or "quota" in error.lower()
    )


async def _call_serpapi(params: dict, api_keys: list[str]) -> Optional[dict]:
    """Call SerpApi, rotating through keys on quota exhaustion.

    Returns the parsed JSON response dict, or None if all keys are exhausted
    or all calls fail.
    """
    for idx, key in enumerate(api_keys):
        attempt_params = {**params, "api_key": key}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(_SERPAPI_BASE, params=attempt_params)
                resp.raise_for_status()
                data = resp.json()

            if _is_quota_exhausted(data):
                logger.warning("SerpApi key %d/%d quota exhausted — trying next key", idx + 1, len(api_keys))
                continue

            return data
        except Exception as exc:
            logger.warning("SerpApi key %d/%d failed: %s", idx + 1, len(api_keys), exc)
            continue

    logger.warning("SerpApi: all %d key(s) exhausted or failed", len(api_keys))
    return None


async def fetch_amazon_product(url: str, api_keys: list[str]) -> Optional[str]:
    """Return formatted product text from SerpApi amazon_product engine.

    Returns None if ASIN can't be extracted or all keys fail/are exhausted.
    """
    asin = _extract_asin(url)
    if not asin:
        logger.debug("No ASIN found in URL: %s", url)
        return None

    params = {
        "engine": "amazon_product",
        "asin": asin,
        "country": _amazon_country(url),
    }

    data = await _call_serpapi(params, api_keys)
    return _amazon_to_text(data, asin) if data else None


async def fetch_google_shopping(title: str, api_keys: list[str]) -> Optional[str]:
    """Return formatted product text from SerpApi Google Shopping engine.

    Used when a site has no JSON-LD price data. Searches by page title.
    Returns None on failure or no results.
    """
    if not title or len(title.strip()) < 5:
        return None

    params = {
        "engine": "google_shopping",
        "q": title[:150],
        "num": 5,
    }

    data = await _call_serpapi(params, api_keys)
    return _shopping_to_text(data, title) if data else None


def _amazon_to_text(data: dict[str, Any], asin: str) -> str:
    parts: list[str] = ["=== SERPAPI AMAZON DATA ==="]

    product = data.get("product_results") or {}

    if product.get("title"):
        parts.append(f"Product: {product['title']}")
    if product.get("brand"):
        parts.append(f"Brand: {product['brand']}")

    # Price — SerpApi uses different keys across API versions
    price_str = _extract_price(product)
    if price_str:
        parts.append(f"Price: {price_str}")

    was_price = product.get("list_price") or product.get("was_price") or product.get("typical_price")
    if was_price:
        parts.append(f"List Price: {was_price}")

    rating = product.get("rating")
    reviews = product.get("reviews_total") or product.get("ratings_total")
    if rating:
        review_str = f" ({reviews:,} reviews)" if isinstance(reviews, int) else (f" ({reviews} reviews)" if reviews else "")
        parts.append(f"Rating: {rating}/5{review_str}")

    if product.get("availability"):
        parts.append(f"Availability: {product['availability']}")

    bullets = product.get("feature_bullets") or []
    if bullets:
        parts.append("Key Features:")
        for b in bullets[:8]:
            if isinstance(b, str) and b.strip():
                parts.append(f"  - {b.strip()}")

    specs = product.get("specifications") or product.get("product_overview") or []
    if specs and isinstance(specs, list):
        parts.append("Specifications:")
        for s in specs[:10]:
            if isinstance(s, dict):
                name = s.get("name") or s.get("key", "")
                value = s.get("value", "")
                if name and value:
                    parts.append(f"  {name}: {value}")

    parts.append(f"ASIN: {asin}")
    parts.append("=== END SERPAPI DATA ===")
    return "\n".join(parts)


def _shopping_to_text(data: dict[str, Any], original_title: str) -> str:
    results = data.get("shopping_results") or []
    if not results:
        return ""

    parts: list[str] = ["=== SERPAPI SHOPPING DATA ==="]
    best = results[0]

    parts.append(f"Product: {best.get('title', original_title)}")
    if best.get("source"):
        parts.append(f"Retailer: {best['source']}")
    if best.get("price"):
        parts.append(f"Price: {best['price']}")
    if best.get("rating"):
        reviews = best.get("reviews", "")
        parts.append(f"Rating: {best['rating']}/5{f' ({reviews} reviews)' if reviews else ''}")
    if best.get("snippet"):
        parts.append(f"Description: {best['snippet']}")

    # Show price spread from top results — useful context
    prices = [r["price"] for r in results[:4] if r.get("price")]
    if len(prices) > 1:
        parts.append(f"Price across retailers: {' | '.join(prices)}")

    parts.append("=== END SERPAPI DATA ===")
    return "\n".join(parts)


def _extract_price(product: dict[str, Any]) -> Optional[str]:
    """Try multiple SerpApi price field names."""
    for field in ("price", "current_price", "sale_price"):
        val = product.get(field)
        if val:
            currency = product.get("currency", "")
            return f"{currency}{val}" if currency and not str(val).startswith(currency) else str(val)

    # Nested pricing object
    pricing = product.get("pricing")
    if isinstance(pricing, dict):
        price = pricing.get("current_price") or pricing.get("price")
        currency = pricing.get("currency", "")
        if price:
            return f"{currency}{price}" if currency else str(price)

    return None
