"""Parse Jina markdown for product signals and emit structured data.

The AI relies on seeing structured product data at the top of the user prompt
(currently from JSON-LD or SerpApi). For arbitrary retailers with neither,
this module extracts product info directly from Jina's markdown using regex,
then prepends it so the AI sees price/name/brand on a plate.

Eliminates the "where is the price in 60K chars of markdown" guessing for
every future site.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# Currency symbol + amount (no word boundary at start because € can be mid-string)
_PRICE_PATTERN = re.compile(
    r'(€|£|\$|₹|Rs\.?|EUR|GBP|USD|INR)\s*([\d,]+(?:\.\d{1,2})?)',
    re.IGNORECASE,
)

# "Was X" / "Regular X" / "RRP X" — used to spot list price near a discounted price
_LIST_PRICE_PATTERN = re.compile(
    r'(?:was|reg(?:ular)?|rrp|original|list)\s*[:\s\-]*(€|£|\$|₹|Rs\.?|EUR|GBP|USD|INR)\s*([\d,]+(?:\.\d{1,2})?)',
    re.IGNORECASE,
)

# Discount percentage: "30% OFF", "save 20%", "-15%"
_DISCOUNT_PATTERN = re.compile(
    r'(\d{1,2})\s*%\s*(?:off|discount)|save\s+(\d{1,2})\s*%|(?<![\d.])(-\d{1,2})\s*%',
    re.IGNORECASE,
)

# Context words that indicate a price is NOT the product price (shipping/delivery thresholds)
_NOISE_CONTEXT = (
    "delivery", "shipping", "free over", "free above", "free on orders",
    "members under", "standard delivery", "express delivery", "next day",
    "minimum order", "spend over", "spend more than",
)

# Markdown image: ![alt text](url) — supports ?query params in URL
_IMG_PATTERN = re.compile(r'!\[([^\]]*)\]\((https?://[^\s\)]+)\)')

# Skip these image categories (logos, icons, UI elements, ads)
_IMG_NOISE_KEYWORDS = (
    "logo", "icon", "favicon", "sprite", "pixel", "advert",
    "banner", "skip to", "menu", "burger", "hamburger",
    "loader", "spinner", "placeholder", "fallback",
)


def extract_product_images(content: str, anchor: int, limit: int = 5) -> list[str]:
    """Extract product image URLs from a window around the product anchor.

    Filters out logos, icons, sprites, and UI assets. Returns up to *limit*
    unique image URLs, ordered by appearance in the markdown.
    """
    if not content:
        return []

    # Look in a generous window around the product section. AllSaints and similar
    # retailers often have the gallery just BEFORE the H1, so widen the look-behind.
    window_start = max(0, anchor - 3000)
    window_end = min(len(content), anchor + 5000)
    window = content[window_start:window_end]

    seen: set[str] = set()
    images: list[str] = []
    for m in _IMG_PATTERN.finditer(window):
        alt, url = m.group(1).lower(), m.group(2)

        # De-dup (strip query params for the comparison key)
        url_key = url.split("?")[0]
        if url_key in seen:
            continue

        # Skip UI noise
        haystack = (alt + " " + url).lower()
        if any(kw in haystack for kw in _IMG_NOISE_KEYWORDS):
            continue

        # Reject only obvious non-images — SVGs (often icons), data URIs, tracking pixels
        if url.startswith("data:"):
            continue
        if re.search(r'\.svg(\?|$)', url, re.IGNORECASE):
            continue
        # Skip CSS/JS files mistakenly captured. Note: NOT excluding .json — many
        # image CDNs (Cloudinary, AllSaints, Shopify) use .json transformation specs
        # that return actual images, so .json in the URL is fine.
        if re.search(r'\.(css|js)(\?|$)', url, re.IGNORECASE):
            continue

        seen.add(url_key)
        images.append(url)
        if len(images) >= limit:
            break

    return images


def parse_jina_product(content: str, url: str, title: Optional[str] = None) -> Optional[str]:
    """Extract structured product data from Jina markdown.

    Anchored parsing — finds the product section (H1 matching title or first
    real heading past nav), then looks for prices INSIDE that section. Respects
    "was/is" markers so "Was €159, is €111" yields price=€111, list=€159
    (not the other way around). Avoids picking related-product prices from the
    navigation/recommendation rails.
    """
    if not content or len(content) < 200:
        return None

    # 1. Find the product section anchor — H1 matching the title (or first H1 past nav)
    anchor = _find_product_anchor(content, title)
    window_start = anchor
    window_end = min(len(content), anchor + 3000)
    window = content[window_start:window_end]

    # 2. Collect all price candidates in the window, with "was/is" markers
    candidates = _find_prices_with_markers(window)
    if not candidates:
        # Widen search to whole content as fallback
        candidates = _find_prices_with_markers(content)
        if not candidates:
            logger.debug("No price candidates found")
            return None

    # 3. Identify list_price (was/RRP) and current_price (is/now/sale)
    current = next((c for c in candidates if c["is_current"]), None)
    list_p = next((c for c in candidates if c["is_list"]), None)

    # 4. If markers absent: assume the FIRST candidate is current price
    if not current and not list_p:
        current = candidates[0]
        # If there's a higher unmarked price nearby, treat it as list price
        higher = [c for c in candidates[1:] if c["amount"] > current["amount"] * 1.05]
        if higher:
            list_p = max(higher, key=lambda x: x["amount"])

    # 5. If only list marker found, next unmarked price is the current
    if list_p and not current:
        for c in candidates:
            if c is not list_p and not c["is_list"]:
                current = c
                break
        if not current:
            current = list_p  # only one price, it's the only price
            list_p = None

    # 6. If only current marker found, find a higher nearby price as list
    if current and not list_p:
        higher = [c for c in candidates if c is not current and c["amount"] > current["amount"] * 1.05]
        if higher:
            list_p = min(higher, key=lambda x: x["amount"])

    if not current:
        return None

    # 7. Discount percentage from anywhere in the window
    discount = None
    dm = _DISCOUNT_PATTERN.search(window)
    if dm:
        for g in dm.groups():
            if g and g.lstrip("-").isdigit():
                discount = f"{g.lstrip('-')}% OFF"
                break

    # 8. Product name — H1 closest to the price (and reasonably close to top of window)
    product_name = _find_product_name(content[:window_start + current["pos"]], window_start + current["pos"], title)

    # 9. Brand from URL
    brand = _brand_from_url(url)

    # 10. Format the block
    lines = ["=== EXTRACTED PRODUCT DATA ==="]
    if product_name:
        lines.append(f"Product: {product_name}")
    if brand:
        lines.append(f"Brand: {brand}")
    lines.append(f"Price: {current['raw']}")
    if list_p and list_p["raw"] != current["raw"]:
        lines.append(f"List Price (was): {list_p['raw']}")
    if discount:
        lines.append(f"Discount: {discount}")
    lines.append("=== END EXTRACTED PRODUCT DATA ===")

    logger.info(
        "Pre-extracted product: %s | %s | price=%s | list=%s | disc=%s",
        product_name or "?", brand or "?", current["raw"], list_p["raw"] if list_p else None, discount,
    )
    return "\n".join(lines)


def _find_product_anchor(content: str, title: Optional[str]) -> int:
    """Return the char position where the product section starts.

    Prefers an H1 matching the page title. Falls back to first H1 past first
    10% of content. Last resort: first 10% boundary itself.
    """
    nav_cutoff = len(content) // 10

    if title:
        cleaned = re.sub(r'^\s*(?:Mens?|Womens?|Kids?|Buy|Shop|The)\s+', '', title, flags=re.IGNORECASE)
        key = re.sub(r'[^\w\s]', '', cleaned.split('|')[0]).strip()[:25]
        if len(key) >= 6:
            pattern = re.compile(rf'^#+\s+[^#\n]*{re.escape(key)}', re.IGNORECASE | re.MULTILINE)
            for m in pattern.finditer(content):
                if m.start() >= nav_cutoff:
                    return m.start()

    # Fallback: first H1 past nav cutoff
    for m in re.finditer(r'^#\s+', content, re.MULTILINE):
        if m.start() >= nav_cutoff:
            return m.start()

    return nav_cutoff


def _find_prices_with_markers(text: str) -> list[dict]:
    """Find all prices in text, annotated with 'was'/'is'/'sale' markers."""
    results = []
    for m in _PRICE_PATTERN.finditer(text):
        symbol, amount_str = m.group(1), m.group(2)
        try:
            amount_val = float(amount_str.replace(",", ""))
        except ValueError:
            continue
        if amount_val < 1 or amount_val > 1_000_000:
            continue

        # Skip noise context (delivery / shipping thresholds)
        ctx = text[max(0, m.start() - 80):m.end() + 40].lower()
        if any(kw in ctx for kw in _NOISE_CONTEXT):
            continue

        # Check preceding word(s) for "was/is/now/sale" markers
        prev = text[max(0, m.start() - 30):m.start()].lower()
        is_list = bool(re.search(r'\b(was|reg\.?|regular|rrp|original|list)\s*[:\-]?\s*$', prev))
        is_current = bool(re.search(r'\b(is|now|sale|price)\s*[:\-]?\s*$', prev))

        results.append({
            "pos": m.start(),
            "amount": amount_val,
            "raw": _normalize_price(symbol, amount_str),
            "is_list": is_list,
            "is_current": is_current,
        })
    return results


def _normalize_price(symbol: str, amount: str) -> str:
    """Format price consistently: symbol + amount with original separators preserved."""
    sym = symbol.strip()
    # Map currency codes to symbols
    code_to_symbol = {"EUR": "€", "GBP": "£", "USD": "$", "INR": "₹"}
    if sym.upper() in code_to_symbol:
        sym = code_to_symbol[sym.upper()]
    elif sym.lower() in ("rs", "rs."):
        sym = "₹"
    return f"{sym}{amount}"


def _find_product_name(content: str, price_pos: int, fallback_title: Optional[str]) -> Optional[str]:
    """Take the closest H1 BEFORE the price (within reason)."""
    # Find all H1 headings before the price
    before_price = content[:price_pos]
    h1_matches = list(re.finditer(r'^#\s+(.+?)\s*$', before_price, re.MULTILINE))
    if h1_matches:
        # Closest one to the price
        closest = h1_matches[-1].group(1).strip()
        # Clean trailing "| Brand" and "(123)" review counts
        closest = closest.split("|")[0].strip()
        closest = re.sub(r'\s*\(\d+\)\s*$', '', closest).strip()
        # Skip if it's a generic site header
        if not re.search(r'(home|welcome|online|shop now)', closest, re.IGNORECASE) and len(closest) > 3:
            return closest

    # Fallback to page title (cleaned)
    if fallback_title:
        return fallback_title.split("|")[0].strip()
    return None


def _brand_from_url(url: str) -> Optional[str]:
    """Use the second-level domain as a brand hint (allsaints.com → AllSaints)."""
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        parts = host.split(".")
        if not parts:
            return None
        candidate = parts[0]
        # Skip generic platforms
        if candidate in ("shop", "store", "shopify", "amazon", "ebay", "etsy"):
            return None
        if len(candidate) < 3:
            return None
        return candidate.capitalize()
    except Exception:
        return None
