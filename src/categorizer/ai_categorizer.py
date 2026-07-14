"""AI categorization engine for Second Brain content.

Uses type-specific extraction prompts for URLs (50+ content types) and
a generic prompt for plain text/media. Falls back to keyword matching
when all AI providers are exhausted.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any

from src.categorizer.prompts import DEFAULT_DISTILL_BUDGET, distill_content, get_system_prompt
from src.categorizer.providers import AllProvidersExhaustedError, call_ai
from src.config.buckets import default_bucket, is_valid_bucket
from src.config.domains import DOMAIN_DEFINITIONS, DOMAINS, register_domain
from src.config.settings import Settings
from src.extractors.url_detector import classify_url_content_type
from src.models.schemas import (
    CategorizedContent,
    ContentType,
    ExtractedContent,
    NoteType,
    SourcePlatform,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory LRU cache for duplicate URL avoidance
# ---------------------------------------------------------------------------

_CACHE_MAX_SIZE = 1000
_categorization_cache: OrderedDict[str, CategorizedContent] = OrderedDict()


def _cache_get(key: str) -> CategorizedContent | None:
    if key in _categorization_cache:
        _categorization_cache.move_to_end(key)
        return _categorization_cache[key]
    return None


def _cache_put(key: str, value: CategorizedContent) -> None:
    _categorization_cache[key] = value
    _categorization_cache.move_to_end(key)
    while len(_categorization_cache) > _CACHE_MAX_SIZE:
        _categorization_cache.popitem(last=False)


def invalidate(key: str) -> bool:
    """Remove *key* from the categorization LRU cache.

    *key* is the URL string for URL captures, or the raw_id for other
    content types — matching the ``cache_key`` logic in ``categorize()``.

    Returns True if the key was present and removed, False if it was not
    cached (safe to call unconditionally before a forced reprocess).

    Cross-file handoff note: ``processor.py`` (debug-agent file) should
    call ``invalidate(capture.url or capture.id)`` before re-processing
    a capture so the next ``categorize()`` call bypasses the stale cache
    entry and runs AI categorization fresh.
    """
    if key in _categorization_cache:
        del _categorization_cache[key]
        logger.debug("Categorization cache invalidated for key: %s", key)
        return True
    return False


# ---------------------------------------------------------------------------
# Task #5 — Known retail domain → force ecommerce url_type
# ---------------------------------------------------------------------------

_KNOWN_RETAIL_DOMAINS = {
    "amazon.com", "amazon.in", "amazon.co.uk", "amazon.ie", "amazon.de", "amazon.fr",
    "nike.com", "nike.in", "adidas.com", "puma.com",
    "thesouledstore.com",
    "footlocker.com", "footlocker.ie", "footlocker.co.uk",
    "asos.com", "wayfair.com", "wayfair.co.uk", "wayfair.ca",
    "myntra.com", "ajio.com", "flipkart.com",
    "zara.com", "hm.com", "uniqlo.com",
    "etsy.com", "ebay.com",
}


def _coerce_retail_url_type(url: str, current_type: str) -> str:
    """If URL hostname is a known retail site, force url_type to 'ecommerce'.

    Returns the (possibly unchanged) url_type string.
    """
    import urllib.parse
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        # Strip 'www.' prefix for matching
        host = host.removeprefix("www.")
        if host in _KNOWN_RETAIL_DOMAINS and current_type != "ecommerce":
            logger.warning(
                "Coercing url_type to 'ecommerce' for known retail domain '%s' (was: '%s')",
                host,
                current_type,
            )
            return "ecommerce"
    except Exception:
        pass
    return current_type


# ---------------------------------------------------------------------------
# Task #4 — URL-type denylist (rules veto AI after categorization)
# ---------------------------------------------------------------------------

# Domains that are never appropriate for the given url_type.
_DENYLIST_BY_URL_TYPE: dict[str, set[str]] = {
    "ecommerce": {
        "computer-science", "data-engineering", "llm", "gen-ai",
        "interview-prep", "applied-ai", "quantum-computing",
        "data-science", "market-intelligence",
    },
    "recipe": {
        "computer-science", "data-engineering", "llm", "gen-ai",
        "interview-prep", "applied-ai", "quantum-computing",
        "data-science", "market-intelligence", "personal-finance", "job-search",
    },
    "restaurant_menu": {
        "computer-science", "data-engineering", "llm", "gen-ai",
        "interview-prep", "applied-ai", "quantum-computing", "data-science",
    },
}

# Keywords that indicate an ecommerce product IS electronics/software and
# should NOT have tech domains stripped (laptops, keyboards, GPUs, SaaS, etc.).
_ELECTRONICS_KEYWORDS = frozenset({
    "laptop", "keyboard", "monitor", "gpu", "processor", "cpu", "motherboard",
    "ssd", "ram", "router", "headphone", "headset", "speaker", "webcam",
    "software", "app", "saas", "course", "subscription", "plugin", "extension",
    "tablet", "ipad", "macbook", "chromebook", "smartwatch", "earbuds",
    "hard drive", "nvme", "gaming", "console", "mouse", "trackpad",
})


def _apply_url_type_denylist(
    url_type: str,
    domains: list[str],
    content: "ExtractedContent",
) -> list[str]:
    """Strip domains that are inappropriate for the given url_type.

    Exception: ecommerce items that are clearly electronics/software keep their
    tech-adjacent domains (laptop, GPU, SaaS, course, etc.).
    """
    denied = _DENYLIST_BY_URL_TYPE.get(url_type)
    if not denied:
        return domains

    # Exception check for ecommerce electronics
    if url_type == "ecommerce":
        searchable = (
            f"{content.title} "
            f"{(content.metadata or {}).get('top_specs', '')} "
            f"{content.content[:500]}"
        ).lower()
        if any(kw in searchable for kw in _ELECTRONICS_KEYWORDS):
            logger.debug(
                "Ecommerce electronics detected for '%s' — skipping denylist",
                content.title,
            )
            return domains

    stripped = [d for d in domains if d not in denied]
    removed = [d for d in domains if d in denied]
    if removed:
        logger.warning(
            "Denylist stripped domains %s from '%s' (url_type=%s)",
            removed,
            content.title,
            url_type,
        )
    return stripped if stripped else domains  # fail-safe: keep original if all stripped


# ---------------------------------------------------------------------------
# Rules-first bucket cascade — skip asking the LLM for a bucket when the
# url_content_type already implies one with high confidence. The LLM is
# still always called for summary/tags/takeaways; only the bucket decision
# is short-circuited. Ambiguous types (generic articles, "unknown") are
# intentionally left out so the LLM keeps deciding those.
# ---------------------------------------------------------------------------

# url_content_type values with an unambiguous consumption intent. Kept
# narrower than DOMAINS/BUCKETS taxonomy — anything not listed here falls
# through to the LLM's own bucket judgement.
_HIGH_CONFIDENCE_BUCKET_TYPES = frozenset({
    "long_video", "short_video", "ecommerce", "recipe", "job",
})


def _rules_bucket(url_content_type: str | None, source_url: str | None) -> str | None:
    """Return a high-confidence deterministic bucket, or None if ambiguous.

    Reuses default_bucket() for the types it already maps correctly
    (ecommerce, recipe, long_video, short_video). default_bucket() checks
    for the url_type string "job_posting", but url_detector.py actually
    classifies job listings as "job" — so that one case is special-cased
    here rather than editing buckets.py (out of scope for this task).
    """
    ut = (url_content_type or "").lower()
    if ut not in _HIGH_CONFIDENCE_BUCKET_TYPES:
        return None
    if ut == "job":
        return "CAREER"
    return default_bucket(ut, None, source_url)


# ---------------------------------------------------------------------------
# Task #6 — Two-pass LLM domain verifier
# ---------------------------------------------------------------------------

_VERIFIER_SYSTEM = """\
You are checking whether a tag accurately describes a piece of content.
Answer ONLY with valid JSON: {"verdict": "YES" or "NO", "reason": "<1 sentence>"}

Answer YES only if the tag describes something CENTRAL to this content — \
meaning the content's primary purpose is about this domain.
Answer NO if the domain applies only because the content mentions it in passing, \
the brand happens to be in that space, or the connection is tangential.

Examples:
- A Nike running shoe tagged "fitness" → YES (the product's purpose is fitness use)
- A Nike running shoe tagged "computer-science" → NO (Nike is not a CS topic)
- A YouTube video about LLM tooling tagged "gen-ai" → YES
- An ecommerce phone case tagged "gen-ai" → NO (a phone case is shopping, not AI)
"""


async def _verify_domain(
    content: "ExtractedContent",
    domain: str,
    definition: str,
    settings: "Settings",
) -> bool:
    """Call the LLM to confirm whether *domain* genuinely fits *content*.

    Returns True (keep) or False (reject). On any error, returns True to
    fail open — better a noisy tag than a lost real one.
    """
    summary_snippet = (content.summary or content.content or "")[:400]
    user_prompt = (
        f"Content:\n"
        f"Title: {content.title}\n"
        f"URL: {content.url or 'N/A'}\n"
        f"Summary: {summary_snippet}\n\n"
        f'Proposed tag: "{domain}"\n'
        f"Tag definition: {definition}"
    )
    try:
        result = await call_ai(
            system_prompt=_VERIFIER_SYSTEM,
            user_prompt=user_prompt,
            settings=settings,
        )
        # call_ai returns a parsed dict; verifier response is also JSON
        # but call_ai already parsed it, so result IS the dict
        verdict = str(result.get("verdict", "YES")).strip().upper()
        reason = result.get("reason", "")
        keep = verdict == "YES"
        if not keep:
            logger.info(
                "Verifier rejected domain '%s' for '%s': %s",
                domain,
                content.title,
                reason,
            )
        return keep
    except Exception as exc:
        logger.warning(
            "Verifier failed for domain '%s' / note '%s' (%s) — keeping candidate",
            domain,
            content.title,
            exc,
        )
        return True  # fail open


async def _verify_domains_parallel(
    content: "ExtractedContent",
    domains: list[str],
    settings: "Settings",
) -> list[str]:
    """Run _verify_domain for each candidate in parallel; return kept domains."""
    if not domains:
        return domains

    import asyncio

    tasks = [
        _verify_domain(
            content,
            d,
            DOMAIN_DEFINITIONS.get(d, f"Content primarily about {d}"),
            settings,
        )
        for d in domains
    ]
    verdicts = await asyncio.gather(*tasks)
    kept = [d for d, ok in zip(domains, verdicts) if ok]
    # Fail-safe: if verifier rejects everything, return original list
    return kept if kept else domains


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_TEXT_SYSTEM_PROMPT = """\
You are a PKM assistant for a personal second brain. \
Respond ONLY with valid JSON.

=== USER CONTEXT ===
Data engineer / AI professional based in Dublin, Ireland. \
Interests: LLMs, gen-AI, data pipelines, MLOps, RAG systems. \
Vegetarian. Active in fitness, finance, interview prep.

Domains: {domain_keys}

=== CRITICAL — ANTI-HALLUCINATION RULE ===
You DO NOT have access to the web, to databases, or to current facts. \
Your training data is stale. You only have what the user wrote in the \
input. Decide which of these two MODES applies, then follow it strictly:

**MODE A — Substantive content** (a thought, quote, paragraph, voice \
memo transcript, journal entry, observation, idea, full sentences). \
Process the text as-is. Extract facts FROM the user's text only. Do \
NOT invent facts about entities the text mentions in passing.

**MODE B — Bare entity reference** (one or two words, a brand name, a \
domain word, a tool name, a person's name, a product name, with no \
explanation — e.g. "PrestaShop", "Linear app", "fly.io", "check out \
n8n", "kubernetes operator"). Treat as a RESEARCH TARGET — the user \
is bookmarking the name to investigate later. Produce:
  - note_type: "fleeting"
  - title: "<entity> — research target"
  - summary: One sentence: "User flagged <entity> as something to \
look into. Send the URL to get a full website-intelligence analysis."
  - key_facts: []   ← MUST be empty. NEVER invent founding dates, \
employee counts, prices, locations, customer counts, founders, or any \
quantitative or proper-noun fact about the entity.
  - action_items: ["Send <best-guess URL, e.g. https://prestashop.com> \
to the bot to get a full website-intelligence pass", "<one specific \
investigation step tied to user context, if obvious>"]
  - open_loops: ["What does <entity> actually do?", "How would it fit \
the user's projects?"]
  - quality_score: 2
  - personal_relevance: 3
  - priority: "low"
  - structured_data: {{}}
  - why_keep: "Flagged as a research target — needs URL for real analysis."
  - tags: ["research-target", "<plausible-domain-hyphen-tag>"]

When in doubt between the modes (e.g. a one-sentence opinion that \
mentions a brand), default to MODE A and treat the entity mention as \
context, NOT as a thing to invent facts about.

=== JSON SHAPE (both modes) ===
{{
  "title": "<descriptive title, max 80 chars>",
  "why_keep": "<1-2 sentences: why future-me would thank present-me>",
  "note_type": "<fleeting|literature|evergreen|reference|recipe|person>",
  "domains": ["<1-3 domains>"],
  "tags": ["<3-6 lowercase hyphenated tags>"],
  "bucket": "<one of: CAREER, WATCH-LONG, WATCH-SHORT, MAKE, SHOP, READ, INSPIRE, DUMP — pick by how user engages, not the topic. DUMP only when nothing else fits.>",
  "quality_score": <1-5>,
  "personal_relevance": <1-5, how relevant to this user>,
  "priority": "<high|medium|low>",
  "summary": "<3-5 sentence synthesis OR the MODE-B sentence>",
  "key_facts": ["<facts FROM the user's text — empty for MODE B>"],
  "action_items": ["<specific next steps: try, apply, read, buy, etc.>"],
  "open_loops": ["<follow-ups, actions, topics to explore>"],
  "structured_data": {{}}
}}

=== RULES (MODE A only) ===
- key_facts must be supported by the user's text. Numbers, dates, \
names quoted verbatim — never pulled from training data about entities \
the text only mentions.
- Be generous with key_facts WHEN THE USER PROVIDED SUBSTANCE. If they \
wrote 30 words, do not pad to 15 facts.
- quality_score: 5=exceptional insight, 3=useful reference, 1=noise
- personal_relevance: 5=directly useful for career/projects, 3=generally useful, 1=off-topic
- priority: high=actionable now, medium=useful reference, low=nice to know
- action_items: specific and concrete, not vague suggestions
- Reuse common tags: tami, data-engineering, llm, gen-ai, dublin, \
mumbai, fitness, recipe-veg, finance, interview-prep"""


def _build_system_prompt(content: ExtractedContent, settings: Settings) -> str:
    """Build the system prompt — type-specific for URLs, generic for text.

    Routing for URLs:
      - A known specialized url_content_type (recipe, github_repo, ...) →
        its dedicated prompt.
      - url_content_type == "unknown" AND the feature flag is on →
        the Website Intelligence prompt (deep 4-phase extract).
      - url_content_type == "unknown" AND flag is off → the shallow
        fallback (legacy behaviour, for emergency rollback).
    """
    if content.url:
        if content.url_content_type and content.url_content_type != "unknown":
            return get_system_prompt(content.url_content_type)
        if settings.enable_website_intelligence:
            return get_system_prompt("website_intelligence")
        return get_system_prompt("unknown")  # legacy thin _FALLBACK_PROMPT

    domain_keys = ", ".join(DOMAINS.keys())
    return _TEXT_SYSTEM_PROMPT.format(domain_keys=domain_keys)


def _build_user_prompt(content: ExtractedContent) -> str:
    """Build the user prompt with content for AI processing.

    Uses distill_content() instead of blind truncation so the AI sees the
    document's shape (title, lead, mid-document structure, ending) rather
    than just the first N characters. Long-form video/podcast transcripts
    get a larger budget since their prompts require detailed section-by-
    section extraction; everything else uses the default budget.
    """
    if content.url_content_type in ("long_video", "podcast_episode"):
        limit = 10000
    elif content.url:
        limit = DEFAULT_DISTILL_BUDGET
    else:
        limit = 3000

    raw = content.content or ""
    truncated = distill_content(raw, max_chars=limit)

    parts = [f"Title: {content.title}"]
    if content.url:
        parts.append(f"URL: {content.url}")
    if content.url_content_type != "unknown":
        parts.append(f"Detected content type: {content.url_content_type}")
    if content.author:
        parts.append(f"Author: {content.author}")
    parts.append(f"Source: {content.source_platform.value}")
    if content.metadata:
        meta_str = ", ".join(
            f"{k}: {v}" for k, v in list(content.metadata.items())[:5]
        )
        parts.append(f"Metadata: {meta_str}")
    parts.append(f"\nContent:\n{truncated}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    return json.loads(text)


def _resolve_folder(domains: list[str]) -> str:
    if domains:
        primary = domains[0]
        if primary in DOMAINS:
            return DOMAINS[primary]["obsidian_folder"]
    return "00_Inbox"


def _parse_note_type(raw: str) -> NoteType:
    raw_lower = raw.strip().lower()
    for nt in NoteType:
        if nt.value == raw_lower:
            return nt
    return NoteType.LITERATURE


def _clamp_quality(score: Any) -> int:
    try:
        val = int(score)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, val))


# ---------------------------------------------------------------------------
# AI API call
# ---------------------------------------------------------------------------

async def _call_ai(
    content: ExtractedContent,
    settings: Settings,
) -> dict[str, Any]:
    preferred = settings.ai_provider if settings.ai_provider != "auto" else None
    return await call_ai(
        system_prompt=_build_system_prompt(content, settings),
        user_prompt=_build_user_prompt(content),
        settings=settings,
        preferred_provider=preferred,
    )


# ---------------------------------------------------------------------------
# Keyword-based fallback
# ---------------------------------------------------------------------------

def _try_confident_keyword_match(
    content: ExtractedContent,
) -> CategorizedContent | None:
    """Skip AI if keyword matching is very confident (5+ hits in top domain)."""
    searchable = (
        f"{content.title} {content.content[:2000]} "
        f"{content.url or ''} {content.author or ''}"
    ).lower()

    scores: dict[str, int] = {}
    for domain_key, info in DOMAINS.items():
        count = sum(1 for kw in info["keywords"] if kw.lower() in searchable)
        if count > 0:
            scores[domain_key] = count

    if not scores:
        return None

    top_domain = max(scores, key=scores.get)  # type: ignore[arg-type]
    top_score = scores[top_domain]

    if top_score < 5:
        return None

    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) > 1 and sorted_scores[1] >= top_score * 0.6:
        return None

    domains = [top_domain]
    if len(sorted_scores) > 1:
        second = [k for k, v in scores.items() if v == sorted_scores[1]][0]
        domains.append(second)

    if "cooking" in domains or "recipe" in searchable:
        note_type = NoteType.RECIPE
    elif content.url:
        note_type = NoteType.LITERATURE
    else:
        note_type = NoteType.FLEETING

    tags = [f"type/{note_type.value}"]
    for d in domains:
        tags.append(f"domain/{d}")
    if content.source_platform != SourcePlatform.UNKNOWN:
        tags.append(f"source/{content.source_platform.value}")

    summary = content.content[:200].strip()
    if len(content.content) > 200:
        summary += "..."

    return CategorizedContent(
        extracted=content,
        note_type=note_type,
        domains=domains,
        tags=tags,
        folder=_resolve_folder(domains),
        key_takeaways=[],
        connections=[],
        quality_score=3,
        categorized_at=datetime.utcnow(),
    )


def _ecommerce_keyword_fallback(content: ExtractedContent) -> CategorizedContent:
    """Fallback for ecommerce URLs when AI is exhausted.

    Parses the `=== EXTRACTED PRODUCT DATA ===` block we prepended during
    extraction to populate structured_data without needing the AI. Produces a
    usable product note even when all Gemini/Groq keys are out for the day.
    """
    text = content.content or ""

    def _extract(field: str) -> Optional[str]:
        m = re.search(rf'^{re.escape(field)}:\s*(.+?)$', text, re.MULTILINE)
        return m.group(1).strip() if m else None

    product = _extract("Product") or content.title
    brand = _extract("Brand")
    price = _extract("Price")
    list_price = _extract("List Price (was)")
    discount = _extract("Discount")

    structured_data = {
        "product": product,
        "brand": brand or "Unknown",
        "price": price or "Not extracted",
        "list_price": list_price or "Not on sale",
        "rating": "Not extracted (AI quota exhausted)",
        "rating_count": 0,
        "top_specs": [],
        "pros": [],
        "cons": [],
        "verdict": "AI quota exhausted today — basic product info only. Will re-categorize fully on next reprocess.",
    }

    key_takeaways = [f"Product: {product}"]
    if brand:
        key_takeaways.append(f"Brand: {brand}")
    if price:
        key_takeaways.append(f"Price: {price}")
    if list_price and list_price != price:
        key_takeaways.append(f"List Price: {list_price}")
    if discount:
        key_takeaways.append(f"Discount: {discount}")

    # Domains: use "fashion" as a sensible default for product pages — better
    # than blindly keyword-matching into politics/ireland/etc.
    domains = ["fashion"] if brand else ["gen-ai"]
    folder = _resolve_folder(domains)

    tags = ["type/reference", f"domain/{domains[0]}", "source/web", "ai/keyword-fallback"]
    if brand:
        tags.append(f"brand/{brand.lower().replace(' ', '-')}")

    logger.info(
        "Ecommerce keyword-fallback for '%s' — brand=%s price=%s (AI exhausted)",
        product, brand, price,
    )

    return CategorizedContent(
        extracted=content,
        note_type=NoteType.REFERENCE,
        domains=domains,
        tags=tags,
        folder=folder,
        key_takeaways=key_takeaways,
        connections=[],
        structured_data=structured_data,
        summary=f"{product}{f' by {brand}' if brand else ''}{f' — {price}' if price else ''}",
        why_keep=f"Product saved for future reference{f' ({discount} discount currently)' if discount else ''}",
        priority="medium",
        personal_relevance=3,
        action_items=[],
        open_loops=[],
        quality_score=3,
        categorized_at=datetime.utcnow(),
    )


def _keyword_fallback(content: ExtractedContent) -> CategorizedContent:
    """Categorize via keyword + semantic matching — always succeeds.

    Special-case: for ecommerce URLs with pre-extracted product data, use
    structured data rather than blindly keyword-matching domains. Avoids
    nonsense like "Nike shoes → politics, ireland" when AI is exhausted.
    """
    # Special-case: ecommerce with structured product data from Jina pre-extraction
    if content.url_content_type == "ecommerce":
        return _ecommerce_keyword_fallback(content)

    searchable = (
        f"{content.title} {content.content[:4000]} "
        f"{content.url or ''} {content.author or ''}"
    ).lower()

    # Try semantic domain matching first (transformer-based, multilingual)
    semantic_domains: list[str] = []
    semantic_tags: list[str] = []
    try:
        from src.search.embeddings import match_domains, extract_keywords, is_available
        if is_available():
            semantic_text = f"{content.title}. {content.content[:2000]}"
            matches = match_domains(semantic_text, top_k=3, threshold=0.3)
            semantic_domains = [m[0] for m in matches]
            semantic_tags = extract_keywords(semantic_text, top_n=6)
            if semantic_domains:
                logger.info(
                    "Semantic fallback for '%s': domains=%s (scores=%s)",
                    content.title,
                    semantic_domains,
                    [(m[0], f"{m[1]:.2f}") for m in matches],
                )
    except Exception:
        pass

    # Keyword counting as secondary signal / fallback
    scores: dict[str, int] = {}
    for domain_key, info in DOMAINS.items():
        count = sum(1 for kw in info["keywords"] if kw.lower() in searchable)
        if count > 0:
            scores[domain_key] = count

    sorted_kw_domains = sorted(scores, key=scores.get, reverse=True)  # type: ignore[arg-type]

    # Merge: semantic domains take priority, keyword domains fill gaps
    if semantic_domains:
        domains = semantic_domains[:3]
    elif sorted_kw_domains:
        domains = sorted_kw_domains[:3]
    else:
        domains = ["gen-ai"]

    if content.content_type == ContentType.CONTACT:
        note_type = NoteType.PERSON
    elif "cooking" in domains or "recipe" in searchable:
        note_type = NoteType.RECIPE
    elif content.url:
        note_type = NoteType.LITERATURE
    else:
        note_type = NoteType.FLEETING

    tags = [f"type/{note_type.value}"]
    for d in domains:
        tags.append(f"domain/{d}")
    if content.source_platform != SourcePlatform.UNKNOWN:
        tags.append(f"source/{content.source_platform.value}")
    # Add extracted keywords as tags
    for kw in semantic_tags[:4]:
        tag = kw.lower().replace(" ", "-")
        if tag not in tags and len(tag) > 2:
            tags.append(tag)

    summary = content.content[:200].strip()
    if len(content.content) > 200:
        summary += "..."

    method = "semantic" if semantic_domains else "keyword"
    logger.info(
        "%s fallback categorized '%s' into domains=%s",
        method.title(), content.title, domains,
    )

    return CategorizedContent(
        extracted=content,
        note_type=note_type,
        domains=domains,
        tags=tags,
        folder=_resolve_folder(domains),
        key_takeaways=[],
        connections=[],
        quality_score=2 if not semantic_domains else 3,
        categorized_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

async def categorize(
    content: ExtractedContent,
    settings: Settings,
) -> CategorizedContent:
    """Categorize extracted content using AI with keyword fallback.

    Never raises. Always returns a CategorizedContent instance.
    Uses type-specific prompts for URLs (50+ content types) and a
    generic prompt for plain text/media.
    """
    cache_key = content.url if content.url else content.raw_id
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for '%s'", cache_key)
        return cached

    # Detect URL content type and attach it
    if content.url and content.url_content_type == "unknown":
        url_type = classify_url_content_type(content.url)

        # Second-pass: if URL pattern didn't match, inspect the extracted content
        # for product signals (JSON-LD Product schema, add-to-cart buttons, SKU paths).
        # This catches retailers not in our domain allowlist (AllSaints, Next, indie
        # Shopify stores, etc.) without triggering the Website Intelligence prompt.
        if url_type == "unknown":
            from src.extractors.url_detector import reclassify_with_content
            url_type = reclassify_with_content(content.url, content.content or "")
            if url_type != "unknown":
                logger.info("URL reclassified by content signals as: %s", url_type)

        # Task #5: known retail hostnames always map to ecommerce regardless of
        # what the URL pattern detector returned.
        url_type = _coerce_retail_url_type(content.url, url_type)

        content = content.model_copy(update={"url_content_type": url_type})
        logger.info("Classified URL as: %s", url_type)

    # Smart skip: if keyword matching is very confident AND content is thin, skip AI.
    # Never skip AI for URLs with structured data (JSON-LD) or detected content types.
    has_rich_content = (
        content.url_content_type != "unknown"
        or "=== STRUCTURED DATA FROM PAGE ===" in (content.content or "")
    )
    if not has_rich_content:
        confident_result = _try_confident_keyword_match(content)
        if confident_result is not None:
            logger.info(
                "Confident keyword match for '%s' — skipping AI call",
                content.title,
            )
            _cache_put(cache_key, confident_result)
            return confident_result

    # Try AI categorization
    try:
        data = await _call_ai(content, settings)

        note_type = _parse_note_type(data.get("note_type", "literature"))
        raw_domains = data.get("domains", [])

        # Validate AI-proposed domains — only accept known taxonomy entries.
        # Unknown domains are logged and silently dropped; register_domain() is
        # NOT called here so AI cannot permanently pollute the taxonomy with
        # hallucinated labels like "tech", "e-commerce", "none", etc.
        validated_domains = []
        for d in raw_domains:
            d_key = d.strip().lower().replace(" ", "-")
            if d_key in DOMAINS:
                validated_domains.append(d_key)
            elif d_key and len(d_key) > 2:
                logger.warning(
                    "AI proposed unknown domain '%s' for note '%s' — rejected (not in taxonomy)",
                    d_key,
                    content.title,
                )
        if not validated_domains:
            validated_domains = ["general"]

        # Task #4: apply URL-type denylist — strip domains the rule layer
        # considers inappropriate for this content type (e.g. ecommerce → no CS/AI).
        validated_domains = _apply_url_type_denylist(
            content.url_content_type or "unknown", validated_domains, content
        )

        # Task #6: two-pass LLM verification — confirm each surviving domain is
        # genuinely central to this content (not tangential or hallucinated).
        # Runs in parallel; fails open on error so real tags are never lost.
        if validated_domains and validated_domains != ["general"]:
            validated_domains = await _verify_domains_parallel(
                content, validated_domains, settings
            )

        quality_score = _clamp_quality(data.get("quality_score", 3))

        tags = data.get("tags", [])
        if not tags or not isinstance(tags, list):
            tags = [f"type/{note_type.value}"]
            for d in validated_domains:
                tags.append(f"domain/{d}")
            if content.source_platform != SourcePlatform.UNKNOWN:
                tags.append(f"source/{content.source_platform.value}")

        folder = _resolve_folder(validated_domains)

        # Store AI-generated title and summary on extracted content
        ai_summary = data.get("summary", "")
        ai_title = data.get("title", "")
        why_keep = data.get("why_keep", "")

        updates: dict[str, Any] = {}
        if ai_summary:
            updates["summary"] = ai_summary
        if ai_title and len(ai_title) > 5:
            updates["title"] = ai_title
        if updates:
            content = content.model_copy(update=updates)

        # Map AI output to CategorizedContent fields
        key_takeaways = data.get("key_facts", data.get("notes", []))
        open_loops = data.get("open_loops", [])
        connections = data.get("connections", data.get("cues", []))
        structured_data = data.get("structured_data", {})
        action_items = data.get("action_items", [])

        # Personal relevance and priority from AI
        personal_relevance = _clamp_quality(data.get("personal_relevance", 3))
        raw_priority = str(data.get("priority", "medium")).strip().lower()
        priority = raw_priority if raw_priority in ("high", "medium", "low") else "medium"

        # Track which AI provider was used
        provider = data.pop("_provider", "ai")
        tags.append(f"ai/{provider}")

        # Rules-first bucket cascade: for high-confidence url_content_types the
        # deterministic mapping wins outright and the AI's bucket guess (if any)
        # is discarded — the LLM is never the source of truth for these types.
        # Otherwise fall back to validating the AI's bucket, then to the
        # deterministic default if it's missing/invalid.
        raw_bucket = str(data.get("bucket", "")).strip().upper()
        rules_bucket = _rules_bucket(content.url_content_type, content.url)
        if rules_bucket is not None:
            bucket = rules_bucket
            bucket_source = "rules"
            if raw_bucket and raw_bucket != rules_bucket:
                logger.debug(
                    "Rules cascade overrides AI bucket '%s' -> '%s' for '%s' (url_type=%s)",
                    raw_bucket, rules_bucket, content.title, content.url_content_type,
                )
        elif is_valid_bucket(raw_bucket):
            bucket = raw_bucket
            bucket_source = "ai"
        else:
            bucket = default_bucket(
                content.url_content_type,
                note_type.value,
                content.url,
            )
            bucket_source = "rules"
            if raw_bucket:
                logger.warning(
                    "AI returned invalid bucket '%s' for '%s' — using default '%s'",
                    raw_bucket, content.title, bucket,
                )
            else:
                logger.debug(
                    "AI did not return bucket for '%s' — using default '%s'",
                    content.title, bucket,
                )
        logger.debug(
            "bucket_source=%s bucket=%s for '%s'", bucket_source, bucket, content.title
        )

        result = CategorizedContent(
            extracted=content,
            note_type=note_type,
            domains=validated_domains,
            tags=tags,
            folder=folder,
            key_takeaways=key_takeaways,
            connections=connections,
            why_keep=why_keep or ai_summary,
            open_loops=open_loops,
            structured_data=structured_data,
            quality_score=quality_score,
            personal_relevance=personal_relevance,
            priority=priority,
            action_items=action_items,
            bucket=bucket,
            categorized_at=datetime.utcnow(),
        )

        logger.info(
            "AI categorized '%s' -> type=%s, url_type=%s, domains=%s, q=%d",
            data.get("title", content.title),
            note_type.value,
            content.url_content_type,
            validated_domains,
            quality_score,
        )

    except AllProvidersExhaustedError:
        logger.warning(
            "All AI providers exhausted — keyword fallback for '%s'",
            content.title,
        )
        result = _keyword_fallback(content)
        result.tags.append("status/needs-ai-review")
        result.tags.append("ai/keyword")
        if not result.bucket:
            fallback_bucket = _rules_bucket(content.url_content_type, content.url) or default_bucket(
                content.url_content_type, None, content.url
            )
            result = result.model_copy(update={"bucket": fallback_bucket})

    except Exception:
        logger.exception(
            "AI categorization failed for '%s'; keyword fallback",
            content.title,
        )
        result = _keyword_fallback(content)
        result.tags.append("ai/keyword")
        if not result.bucket:
            fallback_bucket = _rules_bucket(content.url_content_type, content.url) or default_bucket(
                content.url_content_type, None, content.url
            )
            result = result.model_copy(update={"bucket": fallback_bucket})

    _cache_put(cache_key, result)
    return result
