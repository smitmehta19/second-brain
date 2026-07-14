"""Gemini embeddings utility — best-effort, graceful degradation.

Used for related-notes lookup and duplicate detection. Every function here
must fail SOFT: on any network/API error, or when the feature flag is off,
callers get None/[] back and continue without embeddings. This module never
raises to the caller and never touches the database — it is a pure
best-effort enrichment layer.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Gemini embedContent has an input limit well above this, but we cap
# aggressively to keep embedding calls cheap and fast.
_MAX_INPUT_CHARS = 8000
_EMBED_TIMEOUT = 30


async def embed_text(text: str, settings: Any) -> Optional[list[float]]:
    """Return an embedding vector for *text*, or None on any failure.

    Rotates through settings.all_gemini_keys (the same key pool call_ai()
    uses) so a single quota-exhausted key doesn't block embeddings. Returns
    None immediately when settings.enable_embeddings is False, or when no
    Gemini keys are configured, or when every key fails.
    """
    if not getattr(settings, "enable_embeddings", False):
        return None
    if not text or not text.strip():
        return None

    keys = getattr(settings, "all_gemini_keys", None) or []
    if not keys:
        logger.debug("embed_text: no Gemini keys configured — skipping")
        return None

    model = getattr(settings, "gemini_embedding_model", "text-embedding-004")
    truncated = text[:_MAX_INPUT_CHARS]
    payload = {"content": {"parts": [{"text": truncated}]}}

    for idx, api_key in enumerate(keys):
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:embedContent?key={api_key}"
        )
        try:
            async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            values = (data.get("embedding") or {}).get("values")
            if values:
                return values
            logger.warning(
                "embed_text: empty embedding values (key %d/%d)", idx + 1, len(keys)
            )
        except Exception as exc:
            logger.warning(
                "embed_text: key %d/%d failed (%s) — trying next key",
                idx + 1, len(keys), exc,
            )
            continue

    logger.warning("embed_text: all %d Gemini key(s) failed — returning None", len(keys))
    return None


def cosine(a: list[float], b: list[float]) -> float:
    """Plain-Python cosine similarity (no numpy dependency).

    Returns 0.0 for empty, mismatched-length, or zero-norm vectors rather
    than raising — callers can treat 0.0 as "no similarity signal".
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def find_similar(
    query_vec: list[float],
    candidates: list[tuple[str, list[float]]],
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """Rank *candidates* by cosine similarity to *query_vec*.

    *candidates* is a list of (id, vector) pairs. Returns up to *top_k*
    (id, score) pairs sorted by descending similarity. Returns [] for empty
    input or on any internal error — never raises.
    """
    if not query_vec or not candidates:
        return []
    try:
        scored = [(key, cosine(query_vec, vec)) for key, vec in candidates]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]
    except Exception as exc:
        logger.warning("find_similar failed: %s", exc)
        return []
