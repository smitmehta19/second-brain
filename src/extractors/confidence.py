"""Heuristic confidence scoring for extracted content.

Estimates how trustworthy an extraction is (0.0-1.0) without any I/O, so it
can be computed synchronously right after extraction and stored alongside
the note for the review dashboard / golden-URL smoke tests to surface
low-quality extracts (cookie walls, JS walls, empty pages, etc.).

This is intentionally a *heuristic*, not a model — it trades precision for
speed and zero dependencies. Tune the thresholds/phrases below as new
failure modes are discovered in the review queue.
"""

from __future__ import annotations

import logging

from src.models.schemas import ExtractedContent

logger = logging.getLogger(__name__)

# Phrases that indicate the extractor hit a cookie wall, paywall, login
# gate, or "enable JS" placeholder instead of real content. Checked only
# within the first slice of the content (boilerplate walls almost always
# appear at the very top of the page).
_BOILERPLATE_PHRASES = (
    "enable javascript",
    "enable js",
    "accept cookies",
    "accept all cookies",
    "verify you are human",
    "are you a robot",
    "log in to continue",
    "sign in to continue",
    "please log in to view",
    "subscribe to continue reading",
    "this content is not available",
    "please enable cookies",
    "checking your browser",
    "just a moment...",
    "cloudflare",
    "captcha",
)

# Titles that carry no real signal about the page's actual content.
_GENERIC_TITLES = {"", "untitled", "untitled capture", "no title", "loading..."}

# Only scan this many leading characters for boilerplate/wall phrases —
# real cookie/JS walls always show up immediately, not buried in an article.
_BOILERPLATE_SCAN_WINDOW = 500

# Word-count thresholds for the base score.
_WORDS_EMPTY = 0
_WORDS_VERY_SHORT = 50
_WORDS_SHORT = 150


def score_extraction(extracted: ExtractedContent) -> float:
    """Score how trustworthy an extraction looks, from 0.0 (junk) to 1.0 (great).

    Pure function — no network/db access — so it is safe to call inline in
    the pipeline and in the golden-URL smoke script.

    Heuristics applied (see inline comments for each):
        1. Word count of ``content`` sets the base score.
        2. A real (non-empty, non-generic) title adds a small bonus.
        3. Boilerplate / cookie-wall / JS-wall phrases near the top of the
           content cap the score low, regardless of word count.
        4. Structured data or rich metadata gives a small bonus (indicates
           a JSON-LD / product-parser / platform-API pass succeeded).
    """
    content = (extracted.content or "").strip()
    word_count = len(content.split()) if content else 0

    # 1. Base score from word count -----------------------------------------
    if word_count <= _WORDS_EMPTY:
        # Nothing was extracted at all — no title/metadata bonus can redeem it.
        return 0.0
    if word_count < _WORDS_VERY_SHORT:
        score = 0.2
    elif word_count < _WORDS_SHORT:
        score = 0.5
    else:
        score = 0.8

    # 2. Title quality bonus/penalty -----------------------------------------
    title = (extracted.title or "").strip()
    title_lower = title.lower()
    is_generic_title = (
        title_lower in _GENERIC_TITLES
        or _looks_like_bare_domain(title_lower)
    )
    if title and not is_generic_title:
        score += 0.1
    elif is_generic_title:
        score -= 0.1

    # 3. Boilerplate / wall detection — hard cap regardless of word count ---
    scan_window = content[:_BOILERPLATE_SCAN_WINDOW].lower()
    if any(phrase in scan_window for phrase in _BOILERPLATE_PHRASES):
        score = min(score, 0.3)

    # 4. Structured data / metadata bonus -------------------------------------
    # ExtractedContent doesn't carry a dedicated "structured_data" field (that
    # only appears later, on CategorizedContent) — but a rich `metadata` dict
    # (publish_date, description, view counts, etc.) or extracted images are
    # a strong signal that a dedicated parser (JSON-LD, platform API, product
    # parser) succeeded rather than a generic best-effort text dump.
    metadata = extracted.metadata or {}
    has_images = bool(extracted.images)
    if len(metadata) >= 2 or (metadata and has_images):
        score += 0.05

    return _clamp(score)


def _looks_like_bare_domain(title_lower: str) -> bool:
    """True if the title is just a URL/domain with no article-like content."""
    if not title_lower:
        return False
    # Things like "example.com", "www.example.com/path", "https://..."
    if title_lower.startswith(("http://", "https://", "www.")):
        return True
    # A short single "word" containing a dot and no spaces (bbc.com, cnn.com)
    if " " not in title_lower and "." in title_lower and len(title_lower) < 40:
        return True
    return False


def _clamp(value: float) -> float:
    """Clamp a score into the valid [0.0, 1.0] range."""
    return max(0.0, min(1.0, round(value, 3)))
