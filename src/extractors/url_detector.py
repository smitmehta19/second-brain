"""URL detection, classification, cleaning, and extractor routing."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from src.models.schemas import SourcePlatform

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSRF guard — reject private/loopback/link-local hosts
# ---------------------------------------------------------------------------

def is_safe_external_url(url: str) -> bool:
    """Return True only when *url* is safe to fetch from the server.

    Rejects:
    - Non-http/https schemes
    - Hostnames that resolve to RFC-1918, loopback, link-local, or
      unspecified addresses (SSRF protection for Oracle Cloud metadata,
      Ollama, internal services, etc.)

    All resolved addresses are checked — if ANY is unsafe, the URL is rejected.
    Uses socket.getaddrinfo so it works correctly on Windows (multiple
    address families returned per hostname).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        logger.warning("SSRF guard: could not parse URL: %s", url)
        return False

    if parsed.scheme not in ("http", "https"):
        logger.warning("SSRF guard: rejected non-http scheme '%s' for %s", parsed.scheme, url)
        return False

    hostname = parsed.hostname
    if not hostname:
        logger.warning("SSRF guard: no hostname in URL: %s", url)
        return False

    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        logger.warning("SSRF guard: DNS resolution failed for '%s': %s", hostname, exc)
        return False

    for _family, _type, _proto, _canonname, sockaddr in results:
        raw_addr = sockaddr[0]
        try:
            addr = ipaddress.ip_address(raw_addr)
        except ValueError:
            continue

        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_unspecified
        ):
            logger.warning(
                "SSRF guard: rejected URL %s — resolved to private/loopback address %s",
                url, addr,
            )
            return False

    return True

_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]},;]+", re.IGNORECASE)

# ---------------------------------------------------------------------------
# URL cleaning — strip tracking params
# ---------------------------------------------------------------------------

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "utm_id", "utm_cid", "ref", "fbclid", "gclid", "gad_source",
    "gad_campaignid", "gbraid", "igshid", "si", "feature", "mc_cid",
    "mc_eid", "s_kwcid", "msclkid", "dclid", "yclid", "twclid",
    "li_fat_id", "ttclid", "wbraid", "source", "_hsenc", "_hsmi",
}


def clean_url(url: str) -> str:
    """Strip tracking/analytics params from a URL, keeping the canonical form."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=False)
        cleaned = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        new_query = urlencode(cleaned, doseq=True) if cleaned else ""
        return urlunparse(parsed._replace(query=new_query, fragment=""))
    except Exception:
        return url


# ---------------------------------------------------------------------------
# Domain → SourcePlatform mapping
# ---------------------------------------------------------------------------

_DOMAIN_MAP: list[tuple[str, SourcePlatform]] = [
    ("youtube.com", SourcePlatform.YOUTUBE),
    ("youtu.be", SourcePlatform.YOUTUBE),
    ("m.youtube.com", SourcePlatform.YOUTUBE),
    ("instagram.com", SourcePlatform.INSTAGRAM),
    ("substack.com", SourcePlatform.SUBSTACK),
    ("twitter.com", SourcePlatform.TWITTER),
    ("x.com", SourcePlatform.TWITTER),
    ("t.co", SourcePlatform.TWITTER),
    ("reddit.com", SourcePlatform.REDDIT),
    ("old.reddit.com", SourcePlatform.REDDIT),
    ("github.com", SourcePlatform.GITHUB),
    ("linkedin.com", SourcePlatform.LINKEDIN),
    ("medium.com", SourcePlatform.MEDIUM),
    ("arxiv.org", SourcePlatform.ARXIV),
    ("wikipedia.org", SourcePlatform.WIKIPEDIA),
]

_SHORTENER_DOMAINS = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd"}

# ---------------------------------------------------------------------------
# Fine-grained URL content type classification
# ---------------------------------------------------------------------------

_DOMAIN_TYPE_RULES: list[tuple[str, str]] = [
    # Ecommerce — fashion & general retail
    ("amazon.", "ecommerce"), ("flipkart.com", "ecommerce"),
    ("ebay.com", "ecommerce"), ("etsy.com", "ecommerce"),
    ("myntra.com", "ecommerce"), ("ajio.com", "ecommerce"),
    ("asos.com", "ecommerce"), ("zara.com", "ecommerce"),
    ("hm.com", "ecommerce"), ("uniqlo.com", "ecommerce"),
    ("nike.com", "ecommerce"), ("adidas.com", "ecommerce"),
    ("zalando.com", "ecommerce"), ("zalando.co.uk", "ecommerce"),
    ("currys.co.uk", "ecommerce"), ("johnlewis.com", "ecommerce"),
    ("argos.co.uk", "ecommerce"), ("marks-and-spencer.com", "ecommerce"),
    ("thesouledstore.com", "ecommerce"), ("bewakoof.com", "ecommerce"),
    ("nykaa.com", "ecommerce"), ("meesho.com", "ecommerce"),
    ("shopify.com", "ecommerce"), ("walmart.com", "ecommerce"),
    ("target.com", "ecommerce"), ("bestbuy.com", "ecommerce"),
    ("newegg.com", "ecommerce"), ("wayfair.com", "ecommerce"),
    ("ikea.com", "ecommerce"), ("aliexpress.com", "ecommerce"),
    ("allsaints.com", "ecommerce"), ("gap.com", "ecommerce"),
    ("next.co.uk", "ecommerce"), ("reiss.com", "ecommerce"),
    ("massimodutti.com", "ecommerce"), ("levi.com", "ecommerce"),
    ("puma.com", "ecommerce"), ("reebok.com", "ecommerce"),
    ("newbalance.com", "ecommerce"), ("converse.com", "ecommerce"),
    ("vans.com", "ecommerce"), ("topshop.com", "ecommerce"),
    ("riverisland.com", "ecommerce"), ("boohoo.com", "ecommerce"),
    # Recipe / Food
    ("seriouseats.com", "recipe"), ("bonappetit.com", "recipe"),
    ("cookwithmanali.com", "recipe"), ("allrecipes.com", "recipe"),
    ("food52.com", "recipe"), ("epicurious.com", "recipe"),
    ("budgetbytes.com", "recipe"), ("simplyrecipes.com", "recipe"),
    ("vegrecipesofindia.com", "recipe"), ("hebbarskitchen.com", "recipe"),
    ("indianhealthyrecipes.com", "recipe"), ("whiskaffair.com", "recipe"),
    ("archanaskitchen.com", "recipe"), ("sanjeevkapoor.com", "recipe"),
    ("tarladala.com", "recipe"), ("yummly.com", "recipe"),
    ("delish.com", "recipe"), ("tasty.co", "recipe"),
    ("foodnetwork.com", "recipe"), ("bbcgoodfood.com", "recipe"),
    ("taste.com.au", "recipe"), ("minimalistbaker.com", "recipe"),
    ("smittenkitchen.com", "recipe"), ("pinchofyum.com", "recipe"),
    ("damndelicious.net", "recipe"), ("halfbakedharvest.com", "recipe"),
    # Restaurant / food delivery
    ("zomato.com", "restaurant_menu"), ("swiggy.com", "restaurant_menu"),
    ("opentable.com", "restaurant_menu"), ("deliveroo.com", "restaurant_menu"),
    ("justeat.ie", "restaurant_menu"),
    # News
    ("reuters.com", "news"), ("bbc.com", "news"), ("bbc.co.uk", "news"),
    ("nytimes.com", "news"), ("ft.com", "news"), ("theguardian.com", "news"),
    ("indianexpress.com", "news"), ("irishtimes.com", "news"),
    ("thehindu.com", "news"), ("ndtv.com", "news"), ("rte.ie", "news"),
    ("independent.ie", "news"), ("cnn.com", "news"),
    # Press releases
    ("prnewswire.com", "press_release"), ("businesswire.com", "press_release"),
    ("globenewswire.com", "press_release"),
    # Social
    ("threads.net", "social_post"), ("bsky.app", "social_post"),
    # Podcasts
    ("pocketcasts.com", "podcast_episode"), ("overcast.fm", "podcast_episode"),
    # Code / Dev
    ("gitlab.com", "github_repo"), ("bitbucket.org", "github_repo"),
    ("devhints.io", "cheatsheet"), ("quickref.me", "cheatsheet"),
    ("freecodecamp.org", "tutorial_howto"), ("w3schools.com", "tutorial_howto"),
    ("realpython.com", "tutorial_howto"), ("geeksforgeeks.org", "tutorial_howto"),
    # AI / ML
    ("replicate.com", "ai_model"),
    ("kaggle.com", "dataset"), ("data.gov", "dataset"), ("data.gov.ie", "dataset"),
    # Academic
    ("ssrn.com", "paper"), ("biorxiv.org", "paper"),
    ("jstor.org", "paper"), ("doi.org", "paper"),
    ("semanticscholar.org", "paper"),
    ("patents.google.com", "patent"), ("uspto.gov", "patent"),
    ("espacenet.com", "patent"),
    # Forums / Q&A
    ("news.ycombinator.com", "forum_thread"),
    ("quora.com", "qa_thread"),
    # Reference
    ("britannica.com", "reference"), ("investopedia.com", "reference"),
    ("developer.mozilla.org", "reference"),
    # Software / Reviews
    ("producthunt.com", "saas_product"),
    ("apps.apple.com", "app_listing"), ("play.google.com", "app_listing"),
    ("g2.com", "comparison_review"), ("capterra.com", "comparison_review"),
    ("trustpilot.com", "comparison_review"),
    # Learning
    ("coursera.org", "course"), ("udemy.com", "course"),
    ("edx.org", "course"), ("deeplearning.ai", "course"),
    ("scrimba.com", "course"), ("datacamp.com", "course"),
    # Slides / Docs
    ("speakerdeck.com", "slide_deck"), ("slideshare.net", "slide_deck"),
    ("docs.google.com", "shared_doc"), ("sheets.google.com", "shared_doc"),
    # Design / Visual
    ("figma.com", "design_file"), ("framer.com", "design_file"),
    ("pinterest.com", "image_visual"), ("behance.net", "image_visual"),
    ("dribbble.com", "image_visual"), ("unsplash.com", "image_visual"),
    ("are.na", "image_visual"),
    # Media / Entertainment
    ("goodreads.com", "book_listing"), ("openlibrary.org", "book_listing"),
    ("imdb.com", "movie_show"), ("letterboxd.com", "movie_show"),
    ("rottentomatoes.com", "movie_show"), ("justwatch.com", "movie_show"),
    ("soundcloud.com", "music_track"), ("bandcamp.com", "music_track"),
    # Events
    ("eventbrite.com", "event_listing"), ("meetup.com", "event_listing"),
    ("lu.ma", "event_listing"),
    ("kickstarter.com", "crowdfunding"), ("indiegogo.com", "crowdfunding"),
    ("gofundme.com", "crowdfunding"),
    # Real estate
    ("daft.ie", "real_estate"), ("myhome.ie", "real_estate"),
    ("zillow.com", "real_estate"), ("rightmove.co.uk", "real_estate"),
    ("magicbricks.com", "real_estate"), ("99acres.com", "real_estate"),
    # Travel
    ("booking.com", "hotel_stay"), ("airbnb.com", "hotel_stay"),
    ("skyscanner.", "travel_booking"), ("kayak.com", "travel_booking"),
    ("expedia.com", "travel_booking"),
    ("tripadvisor.com", "map_place"), ("yelp.com", "map_place"),
    # Finance
    ("finance.yahoo.com", "finance_stock"),
    ("tradingview.com", "finance_stock"), ("screener.in", "finance_stock"),
    ("coingecko.com", "crypto_token"), ("coinmarketcap.com", "crypto_token"),
    ("dexscreener.com", "crypto_token"),
    # Health
    ("pubmed.", "health_medical"), ("webmd.com", "health_medical"),
    ("mayoclinic.org", "health_medical"), ("nhs.uk", "health_medical"),
    ("hse.ie", "health_medical"),
    # Weather
    ("weather.com", "weather_forecast"), ("met.ie", "weather_forecast"),
    ("accuweather.com", "weather_forecast"),
    # Jobs
    ("lever.co", "job"), ("greenhouse.io", "job"),
    ("ashbyhq.com", "job"), ("workday.com", "job"),
    # Archive
    ("web.archive.org", "archive"), ("archive.today", "archive"),
]

# Path-based refinements (checked after domain match or for generic domains)
_PATH_RULES: list[tuple[str, str, str]] = [
    # (domain_contains, path_pattern, content_type)
    ("youtube.com", r"/shorts/", "short_video"),
    ("youtube.com", r"/playlist", "youtube_channel_playlist"),
    ("youtube.com", r"/@", "youtube_channel_playlist"),
    ("youtube.com", r"/c/", "youtube_channel_playlist"),
    ("youtube.com", r"/channel/", "youtube_channel_playlist"),
    ("youtube.com", r"/watch", "long_video"),
    ("youtu.be", r"/", "long_video"),
    ("instagram.com", r"/reel/", "short_video"),
    ("instagram.com", r"/reels/", "short_video"),
    ("instagram.com", r"/p/", "social_post"),
    ("instagram.com", r"/stories/", "short_video"),
    ("tiktok.com", r"/video/", "short_video"),
    ("tiktok.com", r"/", "short_video"),
    ("twitter.com", r"/status/", "social_post"),
    ("x.com", r"/status/", "social_post"),
    ("linkedin.com", r"/posts/", "social_post"),
    ("linkedin.com", r"/pulse/", "blog_article"),
    ("linkedin.com", r"/jobs/", "job"),
    ("linkedin.com", r"/in/", "person_profile"),
    ("reddit.com", r"/comments/", "forum_thread"),
    ("reddit.com", r"/r/\w+/?$", "forum_home"),
    ("github.com", r"/releases", "release_changelog"),
    ("huggingface.co", r"/models/", "ai_model"),
    ("huggingface.co", r"/datasets/", "dataset"),
    ("huggingface.co", r"/spaces/", "saas_product"),
    ("spotify.com", r"/episode/", "podcast_episode"),
    ("spotify.com", r"/show/", "podcast_show"),
    ("spotify.com", r"/track/", "music_track"),
    ("spotify.com", r"/album/", "music_track"),
    ("podcasts.apple.com", r"/podcast/", "podcast_episode"),
    ("google.com", r"/maps/", "map_place"),
    ("maps.google", r"/", "map_place"),
    ("google.com", r"/flights", "travel_booking"),
    ("bloomberg.com", r"/quote/", "finance_stock"),
    ("sec.gov", r"/edgar/", "earnings_filing"),
    ("stackoverflow.com", r"/questions/", "forum_thread"),
    ("stackexchange.com", r"/questions/", "qa_thread"),
    ("notion.so", r"/", "shared_doc"),
    ("amazon.", r"/dp/", "ecommerce"),
]

# Generic path patterns (any domain)
_GENERIC_PATH_RULES: list[tuple[str, str]] = [
    (r"/recipe[s]?[/-]", "recipe"),
    (r"recipe", "recipe"),
    (r"/product[s]?/", "ecommerce"),
    (r"/dp/", "ecommerce"),
    (r"/prd/", "ecommerce"),        # ASOS style
    (r"/p/[0-9]", "ecommerce"),     # many European retailers (/p/12345)
    (r"/sku/", "ecommerce"),
    (r"/item/", "ecommerce"),
    (r"/docs?/", "docs_api"),
    (r"/api/", "docs_api"),
    (r"/reference/", "docs_api"),
    (r"/cheatsheet", "cheatsheet"),
    (r"/press/", "press_release"),
    (r"/career[s]?/", "job"),
    (r"/job[s]?/", "job"),
    (r"/about/?$", "person_profile"),
    (r"\.gov(\.[a-z]{2})?/", "gov_official"),
    (r"/status/?$", "status_page"),
]


# ---------------------------------------------------------------------------
# Content-based reclassification (second-pass when URL classification fails)
# ---------------------------------------------------------------------------

_PRODUCT_CONTENT_SIGNALS = [
    re.compile(r'"@type"\s*:\s*"Product"', re.IGNORECASE),
    re.compile(r'"@type"\s*:\s*\["Product"', re.IGNORECASE),
    re.compile(r'"priceCurrency"', re.IGNORECASE),
    re.compile(r'add[\s_-]?to[\s_-]?(cart|bag|basket)', re.IGNORECASE),
    re.compile(r'<button[^>]*(buy[\s-]?now|add[\s-]?to[\s-]?cart)', re.IGNORECASE),
]

_PRODUCT_QUERY_PARAMS = {"pid", "productid", "sku", "itemid", "product_id", "prodid"}


def reclassify_with_content(url: str, content: str) -> str:
    """Second-pass classifier using URL query-params + extracted content signals.

    Returns "ecommerce" when product signals are detected; "unknown" otherwise.
    Called only after the URL-pattern classifier returns "unknown" — gives
    the system a chance to detect product pages on retailers we haven't
    explicitly listed (AllSaints, Next, Gap, indie Shopify stores, etc.).
    """
    if not content and not url:
        return "unknown"

    # URL-level signals — query params
    try:
        qs = parse_qs(urlparse(url).query)
        if any(k.lower() in _PRODUCT_QUERY_PARAMS for k in qs.keys()):
            return "ecommerce"
    except Exception:
        pass

    # URL-level signals — path tokens (catches /browse/product.do, /sku-XXX.html)
    try:
        path = urlparse(url).path.lower()
        if re.search(r'(?:^|/)product[s]?(?:[/.]|$)', path):
            return "ecommerce"
        # SKU as filename: M011EE-162.html, prod-12345.html
        if re.search(r'/[A-Z0-9]{4,}[-_][A-Z0-9]+\.html?$', urlparse(url).path):
            return "ecommerce"
        # Slug + style code: /style/A12345
        if re.search(r'/style/[A-Z]?[0-9]{4,}', urlparse(url).path):
            return "ecommerce"
    except Exception:
        pass

    # Content-level signals — JSON-LD Product schema or add-to-cart UI
    if content:
        for pattern in _PRODUCT_CONTENT_SIGNALS:
            if pattern.search(content):
                return "ecommerce"

    return "unknown"


def classify_url_content_type(url: str) -> str:
    """Classify a URL into one of 50+ fine-grained content types."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().removeprefix("www.")
        path = parsed.path.lower()
    except Exception:
        return "unknown"

    # Path-based rules take priority (more specific)
    for domain_match, path_pattern, ctype in _PATH_RULES:
        if domain_match in hostname and re.search(path_pattern, path, re.IGNORECASE):
            return ctype

    # GitHub special handling: /user/repo vs /user
    if "github.com" in hostname:
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(parts) >= 2:
            return "github_repo"
        elif len(parts) == 1:
            return "github_profile"
        return "github_repo"

    # Substack: individual posts vs homepage
    if "substack.com" in hostname:
        if "/p/" in path or "/post/" in path:
            return "newsletter_post"
        return "blog_article"

    # Medium
    if "medium.com" in hostname or hostname.endswith(".medium.com"):
        return "blog_article"

    # arxiv
    if "arxiv.org" in hostname:
        return "paper"

    # Wikipedia
    if "wikipedia.org" in hostname:
        return "reference"

    # Domain-based rules
    for domain_match, ctype in _DOMAIN_TYPE_RULES:
        if domain_match in hostname or hostname.endswith(domain_match):
            return ctype

    # Docs/API subdomains
    if hostname.startswith(("docs.", "developer.", "api.", "dev.")):
        return "docs_api"

    # Status page subdomains
    if hostname.startswith("status."):
        return "status_page"

    # Generic path rules (any domain)
    for path_pattern, ctype in _GENERIC_PATH_RULES:
        if re.search(path_pattern, path, re.IGNORECASE):
            return ctype

    return "unknown"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def detect_urls(text: str) -> list[str]:
    """Return all URLs found in *text*."""
    return _URL_RE.findall(text)


def classify_url(url: str) -> SourcePlatform:
    """Map a URL to a ``SourcePlatform`` based on its domain."""
    try:
        hostname = urlparse(url).hostname or ""
        hostname = hostname.lower().removeprefix("www.")
    except Exception:
        return SourcePlatform.WEB

    for domain, platform in _DOMAIN_MAP:
        clean = domain.removeprefix("www.")
        if hostname == clean or hostname.endswith("." + clean):
            return platform

    return SourcePlatform.WEB


async def resolve_shortened_url(url: str, *, timeout: float = 10.0) -> str:
    """Follow redirects on shortened URLs and return the final URL."""
    try:
        hostname = urlparse(url).hostname or ""
        hostname = hostname.lower()
    except Exception:
        return url

    if hostname not in _SHORTENER_DOMAINS:
        return url

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(timeout),
        ) as client:
            resp = await client.head(url)
            resolved = str(resp.url)
            logger.debug("Resolved %s -> %s", url, resolved)
            return resolved
    except Exception:
        logger.warning("Failed to resolve shortened URL: %s", url, exc_info=True)
        return url


def _extractor_for_platform(platform: SourcePlatform):
    """Return an extractor **class** for *platform*."""
    if platform == SourcePlatform.YOUTUBE:
        from src.extractors.youtube import YouTubeExtractor
        return YouTubeExtractor
    if platform == SourcePlatform.INSTAGRAM:
        from src.extractors.instagram import InstagramExtractor
        return InstagramExtractor
    if platform == SourcePlatform.SUBSTACK:
        from src.extractors.substack import SubstackExtractor
        return SubstackExtractor
    from src.extractors.web import WebExtractor
    return WebExtractor


async def get_extractor_for_url(url: str):
    """Resolve shorteners, clean URL, classify, and return an extractor instance."""
    resolved = await resolve_shortened_url(url)
    resolved = clean_url(resolved)
    platform = classify_url(resolved)
    extractor_cls = _extractor_for_platform(platform)
    return extractor_cls(), resolved
