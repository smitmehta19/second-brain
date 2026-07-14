"""Notion database sync — creates pages from categorized content."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from src.config.settings import Settings
from src.models.schemas import CategorizedContent

logger = logging.getLogger(__name__)

# Maximum Notion block children per request
_BLOCK_CHUNK_SIZE = 100

# Retry configuration for rate-limit handling
_MAX_RETRIES = 3
_INITIAL_BACKOFF_S = 1.0

# Default content limit — overridden per-call in _build_page_content
_CONTENT_PREVIEW_LIMIT = 8000


# ---------------------------------------------------------------------------
# Pure-Python content cleaning  (zero AI calls)
# ---------------------------------------------------------------------------

_HTML_TAG_RE    = re.compile(r"<[^>]{1,200}>")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINE_RE  = re.compile(r"\n{3,}")
_LONE_URL_RE    = re.compile(r"^\s*https?://\S+\s*$", re.MULTILINE)
_MD_FENCE_RE    = re.compile(r"^```[^\n]*\n?", re.MULTILINE)
_DASH_LINE_RE   = re.compile(r"^[\s\-=_*|~]{3,}$", re.MULTILINE)
_NAV_LINE_RE    = re.compile(r"^(Home|About|Contact|Blog|FAQ|Help|Menu|Search|Login|Register)\s*[|>/»]", re.MULTILINE | re.IGNORECASE)
_EMOJI_SPAM_RE  = re.compile(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF]{3,}")
_REPEATED_RE    = re.compile(r"(.{10,}?)\1{2,}")

_NOISE_PHRASES = frozenset([
    "subscribe", "sign up", "sign in", "log in", "newsletter", "read more",
    "click here", "tap here", "advertisement", "sponsored", "follow us",
    "share this", "cookie", "privacy policy", "terms of service",
    "all rights reserved", "loading...", "please enable javascript",
    "this article", "in this article", "watch now", "listen now",
    "accept cookies", "manage preferences", "we use cookies", "cookie policy",
    "dismiss", "got it", "no thanks", "maybe later", "close this",
    "download the app", "get the app", "open in app", "continue reading",
    "recommended for you", "you may also like", "related articles",
    "trending now", "most popular", "top stories", "more stories",
    "skip to content", "skip to main", "jump to", "table of contents",
    "share on twitter", "share on facebook", "share on linkedin",
    "copy link", "print this", "email this", "save to pocket",
    "comments", "leave a comment", "join the discussion",
    "about the author", "written by", "published by", "updated on",
    "free trial", "upgrade now", "premium", "pro plan", "unlock",
    "notifications", "allow notifications", "turn on notifications",
    "footer", "header", "sidebar", "navigation", "menu",
    "copyright", "©", "all rights reserved",
])


def _clean_content(text: str, limit: int = _CONTENT_PREVIEW_LIMIT) -> str:
    """Clean raw extracted text for Notion — pure string processing, no AI.

    Steps:
    1. Strip HTML tags
    2. Remove lone URL lines
    3. Strip Markdown fences and horizontal rules
    4. Normalise whitespace
    5. Drop lines shorter than 8 chars (stray fragments)
    6. Drop lines that are only web noise phrases
    7. Collapse blank lines
    8. Truncate to *limit* chars at a line boundary
    """
    if not text:
        return ""

    text = _HTML_TAG_RE.sub(" ", text)
    text = _LONE_URL_RE.sub("", text)
    text = _MD_FENCE_RE.sub("", text)
    text = _DASH_LINE_RE.sub("", text)
    text = _NAV_LINE_RE.sub("", text)
    text = _EMOJI_SPAM_RE.sub("", text)
    text = _REPEATED_RE.sub(r"\1", text)

    lines: list[str] = []
    for line in text.splitlines():
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        if len(line) < 12:
            continue
        lower = line.lower()
        if any(phrase in lower for phrase in _NOISE_PHRASES):
            continue
        # Skip lines that are mostly special characters or numbers
        alpha_ratio = sum(c.isalpha() for c in line) / max(len(line), 1)
        if alpha_ratio < 0.4:
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = _BLANK_LINE_RE.sub("\n\n", text).strip()

    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0]
        text += f"\n\n[… truncated at {limit} chars — full text in source]"

    return text


# ---------------------------------------------------------------------------
# Notion rich-text / block helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = 1999) -> str:
    """Notion rich-text blocks have a 2000-char limit (use 1999 for safety)."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _rich_text(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": _truncate(text)}}]


def _heading_block(text: str, level: int = 2) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": _rich_text(text)}}


def _paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


def _labeled_paragraph(label: str, value: str) -> dict:
    """Paragraph with a bold label followed by plain value text."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": _truncate(f"{label}: ", 200)},
                    "annotations": {"bold": True},
                },
                {
                    "type": "text",
                    "text": {"content": _truncate(str(value), 1700)},
                },
            ]
        },
    }


def _bulleted_list_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(text)},
    }


def _callout_block(text: str, emoji: str = "ℹ️", color: str = "gray_background") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        },
    }


def _divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


# ---------------------------------------------------------------------------
# Page body builder
# ---------------------------------------------------------------------------

def _build_page_content(content: CategorizedContent) -> list[dict]:
    """Build the list of Notion blocks for the page body.

    Layout:
    [Callout: source · author · url · content type · captured date]
    [Callout: Why this is worth keeping]
    ── divider ──
    ## Summary
    ## Key Facts (extracted data points)
    ## Type-specific Details (structured_data rendered)
    ## Follow-ups (open loops)
    ── divider ──
    ## Full Content (cleaned raw text, capped at 3000 chars)
    """
    blocks: list[dict] = []
    e = content.extracted

    # ── Detect Website Intelligence note early so metadata label is correct ──
    is_website_intel = bool(
        content.structured_data
        and isinstance(content.structured_data, dict)
        and isinstance(content.structured_data.get("snapshot"), str)
        and isinstance(content.structured_data.get("universal_core"), dict)
    )

    # ── Source info callout — URL rendered as a clickable Notion link ──
    meta_before_url: list[str] = []
    if is_website_intel:
        meta_before_url.append("Type: Website")
    elif e.url_content_type and e.url_content_type != "unknown":
        meta_before_url.append(f"Type: {e.url_content_type.replace('_', ' ').title()}")
    if e.author:
        meta_before_url.append(f"Author: {e.author}")
    date_str = datetime.utcnow().strftime("%Y-%m-%d")

    if e.url:
        prefix = ("  ·  ".join(meta_before_url) + "  ·  ") if meta_before_url else ""
        callout_rich_text = []
        if prefix:
            callout_rich_text.append({"type": "text", "text": {"content": prefix}})
        callout_rich_text.append({
            "type": "text",
            "text": {"content": _truncate(e.url, 1800), "link": {"url": e.url}},
        })
        callout_rich_text.append({"type": "text", "text": {"content": f"  ·  {date_str}"}})
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": callout_rich_text,
                "icon": {"type": "emoji", "emoji": "🔗"},
                "color": "gray_background",
            },
        })
    else:
        meta_before_url.append(f"Captured: {date_str}")
        blocks.append(_callout_block("  ·  ".join(meta_before_url), emoji="🔗", color="gray_background"))

    # ── Why this is worth keeping ──
    if content.why_keep:
        blocks.append(_callout_block(
            content.why_keep, emoji="💡", color="yellow_background",
        ))

    # ── Hoist confidence/caveats to the top so reliability is visible upfront ──
    if is_website_intel:
        caveats = (content.structured_data or {}).get("confidence_caveats")
        if isinstance(caveats, str) and caveats.strip():
            blocks.append(_callout_block(
                f"Confidence: {caveats.strip()}",
                emoji="🔍",
                color="gray_background",
            ))

    # ── Priority / Relevance indicator ──
    if content.priority == "high" or content.personal_relevance >= 4:
        priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(content.priority, "🟡")
        blocks.append(_callout_block(
            f"Priority: {content.priority.upper()}  ·  Relevance: {content.personal_relevance}/5",
            emoji=priority_emoji,
            color="red_background" if content.priority == "high" else "blue_background",
        ))

    # ── Product image (first ecommerce image as a visual anchor) ──
    if e.images and (is_website_intel or e.url_content_type == "ecommerce" or e.url_content_type == "unknown"):
        first_img = e.images[0]
        if first_img and first_img.startswith("http"):
            blocks.append({
                "object": "block",
                "type": "image",
                "image": {"type": "external", "external": {"url": first_img}},
            })

    blocks.append(_divider_block())

    # ── Action Items (shown first when present — most actionable section) ──
    if content.action_items:
        blocks.append(_heading_block("Action Items", level=2))
        for item in content.action_items[:8]:
            blocks.append(_bulleted_list_block(f"☐ {item}"))

    # ── Summary / Key Facts / Follow-ups ──
    # For Website Intelligence notes these three sections are covered by
    # Snapshot / Notable Observations / Gaps & Open Questions in the structured
    # data — rendering both copies makes the page feel duplicated. Skip them.
    if not is_website_intel:
        if e.summary:
            blocks.append(_heading_block("Summary", level=2))
            blocks.append(_paragraph_block(e.summary))

        if content.key_takeaways:
            blocks.append(_heading_block("Key Facts", level=2))
            for fact in content.key_takeaways[:15]:
                blocks.append(_bulleted_list_block(fact))

    # ── Type-specific structured data ──
    if content.structured_data:
        blocks.append(_divider_block())
        blocks.extend(_render_structured_data(
            content.structured_data, e.url_content_type,
        ))

    # ── Follow-ups / Open Loops ──
    # Also skipped for website_intel — gaps_open_questions already renders.
    if content.open_loops and not is_website_intel:
        blocks.append(_heading_block("Follow-ups", level=2))
        for loop in content.open_loops[:5]:
            blocks.append(_bulleted_list_block(f"→ {loop}"))

    # ── Related Topics ──
    if content.connections:
        blocks.append(_heading_block("Related Topics", level=2))
        for item in content.connections[:8]:
            blocks.append(_bulleted_list_block(item))

    # ── Extracted content ──
    has_rich_ai = bool(content.key_takeaways) or bool(content.structured_data)
    if e.content:
        blocks.append(_divider_block())

        # Split JSON-LD structured data from article text
        jsonld_text = ""
        article_text = e.content
        if "=== STRUCTURED DATA FROM PAGE ===" in e.content:
            parts = e.content.split("=== END STRUCTURED DATA ===", 1)
            jsonld_text = parts[0].replace("=== STRUCTURED DATA FROM PAGE ===", "").strip()
            article_text = parts[1].strip() if len(parts) > 1 else ""

        # Always show JSON-LD data — it's the most accurate source
        if jsonld_text and not has_rich_ai:
            blocks.append(_heading_block("Page Data", level=2))
            for line in jsonld_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.endswith(":") or line.startswith("Ingredients") or line.startswith("Method"):
                    blocks.append(_heading_block(line.rstrip(":"), level=3))
                elif line.startswith("  - "):
                    blocks.append(_bulleted_list_block(line.strip("- ").strip()))
                elif line.startswith("  ") and line.lstrip()[0:1].isdigit():
                    blocks.append(_paragraph_block(line.strip()))
                else:
                    blocks.append(_paragraph_block(line))

        # Show cleaned article text — skip when AI gave comprehensive output
        if article_text and not has_rich_ai:
            cleaned = _clean_content(article_text, limit=8000)
            if cleaned:
                blocks.append(_heading_block("Full Content", level=2))
                paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
                for para in paragraphs[:30]:
                    remaining = para
                    while remaining:
                        chunk, remaining = remaining[:1999], remaining[1999:]
                        blocks.append(_paragraph_block(chunk))

    return blocks


def _render_structured_data(data: dict, url_type: str) -> list[dict]:
    """Render type-specific structured_data as Notion blocks."""
    blocks: list[dict] = []
    if not data:
        return blocks

    # Detect recipe from structured_data keys even if url_type is unknown
    is_recipe = url_type == "recipe" or (
        "ingredients" in data and "method" in data
    )

    if is_recipe:
        blocks.append(_heading_block("Recipe Details", level=2))
        for field in ("cuisine", "dish_type", "yield", "total_time", "prep_time", "cook_time", "diet"):
            if data.get(field):
                blocks.append(_labeled_paragraph(
                    field.replace("_", " ").title(), data[field],
                ))
        if data.get("calories") or data.get("nutrition"):
            nutrition = data.get("nutrition") or data.get("calories", "")
            blocks.append(_labeled_paragraph("Nutrition", nutrition))
        if data.get("ingredients"):
            blocks.append(_heading_block("Ingredients", level=3))
            for ing in data["ingredients"]:
                blocks.append(_bulleted_list_block(ing))
        if data.get("method"):
            blocks.append(_heading_block("Method", level=3))
            for i, step in enumerate(data["method"], 1):
                blocks.append(_paragraph_block(f"{i}. {step}"))
        if data.get("tips"):
            blocks.append(_heading_block("Tips", level=3))
            for tip in data["tips"]:
                blocks.append(_bulleted_list_block(f"💡 {tip}"))
        if data.get("substitutions"):
            blocks.append(_heading_block("Substitutions", level=3))
            for sub in data["substitutions"]:
                blocks.append(_bulleted_list_block(sub))
        return blocks

    if url_type in ("long_video", "podcast_episode"):
        blocks.append(_heading_block("Details", level=2))
        for field in ("speakers", "thesis", "duration"):
            val = data.get(field)
            if not val:
                continue
            if isinstance(val, list):
                val = ", ".join(val)
            blocks.append(_labeled_paragraph(
                field.replace("_", " ").title(), val,
            ))
        if data.get("beats"):
            blocks.append(_heading_block("Beats", level=3))
            for beat in data["beats"]:
                blocks.append(_bulleted_list_block(beat))
        if data.get("sections"):
            blocks.append(_heading_block("Detailed Breakdown", level=2))
            for section in data["sections"]:
                text = str(section)
                remaining = text
                while remaining:
                    chunk, remaining = remaining[:1999], remaining[1999:]
                    blocks.append(_paragraph_block(chunk))
        if data.get("takeaways"):
            blocks.append(_heading_block("Takeaways", level=3))
            for t in data["takeaways"]:
                blocks.append(_bulleted_list_block(t))
        if data.get("mentions"):
            blocks.append(_heading_block("Mentions", level=3))
            for m in data["mentions"]:
                blocks.append(_bulleted_list_block(m))
        if data.get("personal_assessment"):
            blocks.append(_callout_block(
                data["personal_assessment"], emoji="🎯", color="blue_background",
            ))
        return blocks

    if url_type == "ecommerce":
        blocks.append(_heading_block("Product Details", level=2))
        for field in ("product", "brand", "price", "list_price", "rating"):
            if data.get(field):
                blocks.append(_labeled_paragraph(
                    field.replace("_", " ").title(), data[field],
                ))
        if data.get("top_specs"):
            blocks.append(_heading_block("Specs", level=3))
            for spec in data["top_specs"]:
                blocks.append(_bulleted_list_block(spec))
        if data.get("pros"):
            blocks.append(_heading_block("Pros", level=3))
            for pro in data["pros"]:
                blocks.append(_bulleted_list_block(f"✓ {pro}"))
        if data.get("cons"):
            blocks.append(_heading_block("Cons", level=3))
            for con in data["cons"]:
                blocks.append(_bulleted_list_block(f"✗ {con}"))
        if data.get("verdict"):
            blocks.append(_callout_block(data["verdict"], emoji="⚖️"))
        return blocks

    if url_type == "github_repo":
        blocks.append(_heading_block("Repository", level=2))
        for field in ("purpose", "language", "stars", "license", "maintenance"):
            if data.get(field):
                blocks.append(_labeled_paragraph(
                    field.replace("_", " ").title(), data[field],
                ))
        if data.get("features"):
            blocks.append(_heading_block("Features", level=3))
            for feat in data["features"]:
                blocks.append(_bulleted_list_block(feat))
        if data.get("install"):
            blocks.append(_heading_block("Quick Start", level=3))
            blocks.append(_paragraph_block(data["install"]))
        return blocks

    if url_type == "job":
        blocks.append(_heading_block("Job Details", level=2))
        for field in ("role", "company", "location", "remote", "compensation"):
            if data.get(field):
                blocks.append(_labeled_paragraph(
                    field.replace("_", " ").title(), data[field],
                ))
        if data.get("must_haves"):
            blocks.append(_heading_block("Must-haves", level=3))
            for req in data["must_haves"]:
                blocks.append(_bulleted_list_block(req))
        if data.get("fit_assessment"):
            blocks.append(_callout_block(
                f"Fit assessment: {data['fit_assessment']}", emoji="🎯",
            ))
        return blocks

    # Website Intelligence — deep 4-phase extract.
    # Detect by url_type OR by shape (so manual reprocesses still render
    # correctly if the url_content_type is missing).
    is_website_intel = url_type == "website_intelligence" or (
        isinstance(data.get("snapshot"), str)
        and isinstance(data.get("universal_core"), dict)
    )
    if is_website_intel:
        return _render_website_intelligence(data)

    # Generic fallback: render all key-value pairs
    blocks.append(_heading_block("Details", level=2))
    for key, value in data.items():
        if isinstance(value, list) and value:
            blocks.append(_labeled_paragraph(key.replace("_", " ").title(), ""))
            for item in value[:10]:
                blocks.append(_bulleted_list_block(str(item)))
        elif isinstance(value, str) and value:
            blocks.append(_labeled_paragraph(
                key.replace("_", " ").title(), value,
            ))
    return blocks


def _render_website_intelligence(data: dict) -> list[dict]:
    """Render the 7-section Website Intelligence report as Notion blocks.

    Expected schema (all sections optional; empty ones are skipped so the
    page never shows hollow headings):

      snapshot              : str
      classification        : {site_type, unit_of_value, primary_cta, business_model}
      universal_core        : {identity: {...}, purpose: {...}, ...18 categories}
      custom_lens           : [{"field": str, "value": str}, ...]
      notable_observations  : [str]
      gaps_open_questions   : [str]
      confidence_caveats    : str

    ``observations`` (Phase 1 working notes) is intentionally NOT rendered —
    it lives in structured_data for inspection but doesn't belong on the page.
    """
    blocks: list[dict] = []

    # 1. Snapshot
    snapshot = data.get("snapshot")
    if isinstance(snapshot, str) and snapshot.strip():
        blocks.append(_heading_block("Snapshot", level=2))
        blocks.append(_paragraph_block(snapshot.strip()))

    # 2. Classification
    classification = data.get("classification")
    if isinstance(classification, dict) and any(classification.values()):
        blocks.append(_heading_block("Classification", level=2))
        for key in ("site_type", "unit_of_value", "primary_cta", "business_model"):
            val = classification.get(key)
            if isinstance(val, str) and val.strip():
                blocks.append(_labeled_paragraph(
                    key.replace("_", " ").title(), val.strip(),
                ))

    # 3. Universal Core — skip empty categories entirely.
    universal_core = data.get("universal_core")
    if isinstance(universal_core, dict):
        rendered_any_category = False
        category_blocks: list[dict] = []
        # Preserve a sensible canonical order.
        canonical_order = (
            "identity", "purpose", "audience", "geography_language",
            "scale_signals", "value_proposition", "offerings",
            "pricing_model", "conversion_paths", "trust_signals",
            "team_leadership", "content_footprint", "social_presence",
            "tech_infrastructure", "tone_brand_voice", "recency",
            "legal_compliance", "gaps_red_flags",
        )
        ordered_keys = list(canonical_order) + [
            k for k in universal_core.keys() if k not in canonical_order
        ]
        for cat_key in ordered_keys:
            sub = universal_core.get(cat_key)
            if not isinstance(sub, dict):
                # Tolerate the model emitting a string instead of a dict.
                if isinstance(sub, str) and sub.strip():
                    category_blocks.append(_heading_block(
                        cat_key.replace("_", " ").title(), level=3,
                    ))
                    category_blocks.append(_paragraph_block(sub.strip()))
                    rendered_any_category = True
                continue
            populated = [(k, v) for k, v in sub.items() if _is_populated(v)]
            if not populated:
                continue
            category_blocks.append(_heading_block(
                cat_key.replace("_", " ").title(), level=3,
            ))
            for sub_key, sub_val in populated:
                category_blocks.append(_labeled_paragraph(
                    sub_key.replace("_", " ").title(),
                    _stringify(sub_val),
                ))
            rendered_any_category = True
        if rendered_any_category:
            blocks.append(_heading_block("Universal Core", level=2))
            blocks.extend(category_blocks)

    # 4. Custom Lens
    custom_lens = data.get("custom_lens")
    if isinstance(custom_lens, list) and custom_lens:
        rendered_lens: list[dict] = []
        for item in custom_lens:
            if isinstance(item, dict):
                field = str(item.get("field", "")).strip()
                value = _stringify(item.get("value", "")).strip()
                if field and value:
                    rendered_lens.append(_bulleted_list_block(f"{field}: {value}"))
            elif isinstance(item, str) and item.strip():
                rendered_lens.append(_bulleted_list_block(item.strip()))
        if rendered_lens:
            blocks.append(_heading_block("Custom Lens", level=2))
            blocks.extend(rendered_lens)

    # 5. Notable Observations
    observations_list = data.get("notable_observations")
    if isinstance(observations_list, list) and observations_list:
        rendered_obs = [
            _bulleted_list_block(str(o).strip())
            for o in observations_list
            if str(o).strip()
        ]
        if rendered_obs:
            blocks.append(_heading_block("Notable Observations", level=2))
            blocks.extend(rendered_obs)

    # 6. Gaps & Open Questions
    gaps = data.get("gaps_open_questions")
    if isinstance(gaps, list) and gaps:
        rendered_gaps = [
            _bulleted_list_block(str(g).strip())
            for g in gaps
            if str(g).strip()
        ]
        if rendered_gaps:
            blocks.append(_heading_block("Gaps & Open Questions", level=2))
            blocks.extend(rendered_gaps)

    # 7. Confidence & Caveats — NOT rendered here. It is hoisted to the top
    # of the page in _build_page_content so the reader sees the data-quality
    # signal before scrolling through the rest. The raw string still lives
    # in structured_data for SQLite and any future renderers.
    return blocks


# Strings the model emits when it doesn't actually know — treat as empty so
# they don't bloat the Notion page. Comparison is case-insensitive on the
# stripped value.
_NOISE_STRINGS = frozenset({
    "n/a", "na", "none", "null", "tbd", "unknown", "not known",
    "not disclosed", "not applicable", "not provided", "not specified",
    "not available", "not mentioned", "not stated", "not listed",
    "not given", "not shown", "no information", "no data",
    "false",  # boolean-as-string for gaps_red_flags etc. — uninformative on its own
})


def _is_populated(value) -> bool:
    """True if the value carries real information.

    False for: None, empty string/list/dict, the model's polite "I don't know"
    placeholders ("Not disclosed", "Not applicable", "N/A", "Unknown"...),
    and dicts whose every sub-value is itself unpopulated (so nested noise
    bubbles up).
    """
    if value is None:
        return False
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return False
        if s in _NOISE_STRINGS:
            return False
        # Strip phrases like "not disclosed on the X page" → still noise
        if s.startswith(("not disclosed", "not applicable", "not provided",
                         "not specified", "not available", "not mentioned",
                         "no information")):
            return False
        return True
    if isinstance(value, dict):
        return any(_is_populated(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_is_populated(v) for v in value)
    return True


def _stringify(value) -> str:
    """Coerce a structured_data value to a human-readable string for a Notion paragraph."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value if _is_populated(v))
    if isinstance(value, dict):
        return ", ".join(
            f"{k}: {_stringify(v)}" for k, v in value.items() if _is_populated(v)
        )
    return str(value)


# ---------------------------------------------------------------------------
# Page properties
# ---------------------------------------------------------------------------

def _build_page_properties(content: CategorizedContent) -> dict:
    """Map CategorizedContent fields to Notion database properties."""
    e = content.extracted
    today = datetime.utcnow().strftime("%Y-%m-%d")
    primary_domain = content.domains[0] if content.domains else "general"

    # Determine initial status based on priority
    status = "To Review" if content.priority == "high" else "Inbox"

    # Content type label
    ct_label = e.url_content_type.replace("_", " ").title() if e.url_content_type != "unknown" else ""

    properties: dict = {
        "Name": {"title": _rich_text(e.title)},
        "Domain": {"select": {"name": primary_domain}},
        "Tags": {
            "multi_select": [{"name": tag} for tag in content.tags[:25]],
        },
        "Status": {"select": {"name": status}},
        "Note Type": {"select": {"name": content.note_type.value}},
        "Priority": {"select": {"name": content.priority.capitalize()}},
        "Quality": {"number": content.quality_score},
        "Relevance": {"number": content.personal_relevance},
        "Created": {"date": {"start": today}},
    }

    if ct_label:
        properties["Content Type"] = {"select": {"name": ct_label}}

    if e.url:
        properties["Source URL"] = {"url": e.url}

    if content.bucket:
        properties["Bucket"] = {"multi_select": [{"name": content.bucket}]}

    return properties


_BUCKET_PROP_MISSING_RE = re.compile(r"Bucket is not a property", re.IGNORECASE)


async def _ensure_bucket_property(notion, database_id: str) -> bool:
    """Add a 'Bucket' multi_select property to the database schema if missing.

    Uses the notion-client 3.x data-source API: the database is retrieved to
    enumerate its data sources, then the first source's schema is updated.
    Returns True if the schema now (or already) has the property.
    """
    try:
        db = await _retry_with_backoff(
            lambda: notion.databases.retrieve(database_id=database_id)
        )
        sources = db.get("data_sources") or []
        if not sources:
            logger.warning("No data sources on database %s — cannot add Bucket property", database_id)
            return False
        ds_id = sources[0]["id"]
        await _retry_with_backoff(
            lambda: notion.data_sources.update(
                data_source_id=ds_id,
                properties={"Bucket": {"multi_select": {}}},
            )
        )
        logger.info("Added 'Bucket' multi_select property to Notion data source %s", ds_id)
        return True
    except Exception:
        logger.exception("Failed to add Bucket property to Notion schema")
        return False


async def update_page_buckets(
    page_id: str,
    buckets: list[str],
    settings: Settings,
) -> bool:
    """Sync a note's bucket assignment to its Notion page (property-only update).

    Called when buckets change on the dashboard, so Notion stays consistent
    with local curation. If the database schema lacks the Bucket property,
    it is added once and the update retried. Returns True on success; all
    failures are swallowed (local state is authoritative).
    """
    if not settings.notion_api_key or not settings.enable_notion_sync:
        return False

    try:
        from notion_client import AsyncClient
    except ImportError:
        return False

    notion = AsyncClient(auth=settings.notion_api_key)
    props = {"Bucket": {"multi_select": [{"name": b} for b in buckets]}}

    for attempt in (1, 2):
        try:
            await _retry_with_backoff(
                lambda: notion.pages.update(page_id=page_id, properties=props)
            )
            logger.info("Synced buckets %s to Notion page %s", buckets, page_id)
            return True
        except Exception as exc:
            if attempt == 1 and _BUCKET_PROP_MISSING_RE.search(str(exc)):
                database_id = (
                    settings.notion_inbox_database_id
                    or settings.notion_resources_database_id
                )
                if database_id and await _ensure_bucket_property(notion, database_id):
                    continue
            logger.warning("Failed to sync buckets to Notion page %s: %s", page_id, exc)
            return False
    return False


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

async def _retry_with_backoff(coro_factory, *, max_retries: int = _MAX_RETRIES):
    """Call *coro_factory()* with exponential backoff on rate-limit errors."""
    backoff = _INITIAL_BACKOFF_S
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            error_code = getattr(exc, "status", None) or getattr(exc, "code", None)
            if error_code == 429 or "rate" in str(exc).lower():
                logger.warning(
                    "Notion rate-limited (attempt %d/%d), retrying in %.1fs",
                    attempt, max_retries, backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            raise

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def save_to_notion(
    content: CategorizedContent,
    settings: Settings,
) -> Optional[str]:
    """Create a Notion page for the categorized content.

    Returns the page ID on success, or ``None`` if Notion is not configured.
    The returned page ID builds a direct deep-link:
        https://www.notion.so/<page_id_no_hyphens>
    """
    if not settings.notion_api_key:
        logger.debug("Notion API key not configured — skipping sync.")
        return None

    if not settings.enable_notion_sync:
        logger.debug("Notion sync disabled — skipping.")
        return None

    database_id = settings.notion_inbox_database_id or settings.notion_resources_database_id
    if not database_id:
        logger.warning("No Notion database ID configured — skipping sync.")
        return None

    try:
        from notion_client import AsyncClient
    except ImportError:
        logger.error(
            "notion-client package not installed. "
            "Install with: pip install notion-client"
        )
        return None

    notion = AsyncClient(auth=settings.notion_api_key)

    properties = _build_page_properties(content)
    children = _build_page_content(content)

    first_batch = children[:_BLOCK_CHUNK_SIZE]
    remaining_batches = [
        children[i : i + _BLOCK_CHUNK_SIZE]
        for i in range(_BLOCK_CHUNK_SIZE, len(children), _BLOCK_CHUNK_SIZE)
    ]

    try:
        page = await _retry_with_backoff(
            lambda: notion.pages.create(
                parent={"database_id": database_id},
                properties=properties,
                children=first_batch,
            )
        )
        page_id: str = page["id"]
        logger.info("Created Notion page %s for '%s'", page_id, content.extracted.title)

        for batch in remaining_batches:
            await _retry_with_backoff(
                lambda b=batch: notion.blocks.children.append(
                    block_id=page_id,
                    children=b,
                )
            )

        return page_id

    except Exception:
        logger.exception("Failed to save to Notion")
        return None


async def archive_page(page_id: str, settings: Settings) -> bool:
    """Archive a Notion page by setting archived=True (moves it to Notion trash).

    The page is recoverable from Notion trash for 30 days.
    Returns True on success, False on any failure (errors are swallowed so
    the caller's local delete is never rolled back by a Notion failure).
    """
    if not settings.notion_api_key:
        return False

    try:
        from notion_client import AsyncClient
    except ImportError:
        logger.warning("notion-client not installed — cannot archive page %s", page_id)
        return False

    notion = AsyncClient(auth=settings.notion_api_key)
    try:
        await _retry_with_backoff(
            lambda: notion.pages.update(page_id=page_id, archived=True)
        )
        logger.info("Archived Notion page %s", page_id)
        return True
    except Exception as exc:
        logger.warning("Failed to archive Notion page %s: %s", page_id, exc)
        return False


async def update_notion_page(
    page_id: str,
    content: CategorizedContent,
    settings: Settings,
) -> Optional[str]:
    """Update an existing Notion page with new content.

    Preserves the page URL and any external references. Steps:
    1. Update page properties
    2. Delete all existing children blocks
    3. Append new content blocks

    Returns the page_id on success, None on failure (falls back to create).
    """
    if not settings.notion_api_key:
        return None

    try:
        from notion_client import AsyncClient
    except ImportError:
        return None

    notion = AsyncClient(auth=settings.notion_api_key)

    properties = _build_page_properties(content)
    children = _build_page_content(content)

    try:
        # 1. Update properties
        await _retry_with_backoff(
            lambda: notion.pages.update(page_id=page_id, properties=properties)
        )

        # 2. Delete all existing children
        existing = await _retry_with_backoff(
            lambda: notion.blocks.children.list(block_id=page_id)
        )
        for block in existing.get("results", []):
            try:
                await _retry_with_backoff(
                    lambda bid=block["id"]: notion.blocks.delete(block_id=bid)
                )
            except Exception:
                pass  # Non-critical — some blocks may be undeletable
            await asyncio.sleep(0.35)  # Stay within 3 req/s

        # 3. Append new content in batches
        for i in range(0, len(children), _BLOCK_CHUNK_SIZE):
            batch = children[i : i + _BLOCK_CHUNK_SIZE]
            await _retry_with_backoff(
                lambda b=batch: notion.blocks.children.append(
                    block_id=page_id, children=b,
                )
            )

        logger.info("Updated Notion page %s for '%s'", page_id, content.extracted.title)
        return page_id

    except Exception:
        logger.exception("Failed to update Notion page %s — will create new", page_id)
        return None
