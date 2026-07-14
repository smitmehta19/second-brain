"""Jina AI Reader — primary web extractor for the Second Brain bot.

Jina runs a headless browser on its servers, handling both static pages
and JS-rendered SPAs (React/Vue/Angular). Returns clean Markdown.

Tier structure (in order):
  1. Jina with API key(s) — 1M tokens/month per key, rotates on exhaustion
  2. Jina anonymous — ~200 RPM, no monthly limit but lower rate tolerance
  3. Falls back to trafilatura/readability (caller's responsibility)

Add keys via .env:
  JINA_API_KEY=key1
  JINA_API_KEYS=key2,key3   (comma-separated extra keys)

Oracle Cloud: no local deps, just httpx.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_JINA_BASE = "https://r.jina.ai/"
THIN_CONTENT_THRESHOLD = 400


def is_thin_content(content: str) -> bool:
    return len(content.strip()) < THIN_CONTENT_THRESHOLD


# ---------------------------------------------------------------------------
# Per-key quota tracker (resets daily at midnight UTC, like AI providers)
# ---------------------------------------------------------------------------

class _JinaKeyTracker:
    def __init__(self):
        self._exhausted: dict[int, str] = {}  # key_idx → date
        self._today: str = ""

    def _reset_if_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today:
            self._exhausted.clear()
            self._today = today

    def mark_exhausted(self, idx: int):
        self._reset_if_new_day()
        self._exhausted[idx] = self._today
        logger.warning("Jina key %d marked exhausted for today", idx)

    def is_exhausted(self, idx: int) -> bool:
        self._reset_if_new_day()
        return idx in self._exhausted

    def all_exhausted(self, count: int) -> bool:
        self._reset_if_new_day()
        return all(i in self._exhausted for i in range(count))


_tracker = _JinaKeyTracker()


def _is_quota_error(status: int, body: str) -> bool:
    if status == 402:
        return True
    if status == 429:
        body_lower = body.lower()
        return any(kw in body_lower for kw in ("quota", "monthly", "credit", "limit exceeded", "tokens"))
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_via_jina(
    url: str,
    api_keys: Optional[list[str]] = None,
    *,
    anonymous_fallback: bool = True,
) -> Optional[str]:
    """Fetch *url* via Jina Reader and return clean Markdown.

    Tries each key in *api_keys* in order, rotating on quota exhaustion.
    If all keys exhausted (or no keys), falls back to anonymous Jina
    (works but lower rate limit) when *anonymous_fallback* is True.

    Returns None only when Jina itself is unreachable or returns empty content.
    """
    keys = api_keys or []
    attempts: list[tuple[Optional[str], Optional[int]]] = [
        (keys[i], i) for i in range(len(keys)) if not _tracker.is_exhausted(i)
    ]
    if anonymous_fallback and (not keys or _tracker.all_exhausted(len(keys))):
        attempts.append((None, None))  # anonymous — no key, no index

    for api_key, key_idx in attempts:
        label = f"key {key_idx + 1}/{len(keys)}" if key_idx is not None else "anonymous"
        result = await _call_jina(url, api_key, label)

        if result == "QUOTA_EXHAUSTED" and key_idx is not None:
            _tracker.mark_exhausted(key_idx)
            continue  # try next key

        if result and result != "QUOTA_EXHAUSTED":
            return result

    logger.warning("Jina: all keys and anonymous fallback failed for %s", url)
    return None


async def _call_jina(url: str, api_key: Optional[str], label: str) -> Optional[str]:
    """Single Jina call. Returns content string, 'QUOTA_EXHAUSTED', or None."""
    from src.extractors.url_detector import is_safe_external_url
    if not is_safe_external_url(url):
        logger.warning("Jina: blocked unsafe URL (SSRF guard): %s", url)
        return None

    jina_url = f"{_JINA_BASE}{url}"
    headers = {
        "Accept": "text/plain",
        "X-Return-Format": "markdown",
        "X-Timeout": "45",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(jina_url, headers=headers)

        if _is_quota_error(resp.status_code, resp.text):
            logger.warning("Jina %s quota exhausted", label)
            return "QUOTA_EXHAUSTED"

        resp.raise_for_status()
        content = resp.text.strip()
        if len(content) < 100:
            logger.debug("Jina (%s) near-empty response for %s", label, url)
            return None

        logger.info("Jina (%s): %d chars for %s", label, len(content), url)
        return content

    except httpx.HTTPStatusError as exc:
        if _is_quota_error(exc.response.status_code, exc.response.text):
            return "QUOTA_EXHAUSTED"
        logger.warning("Jina (%s) HTTP error for %s: %s", label, url, exc)
        return None
    except Exception as exc:
        logger.warning("Jina (%s) failed for %s: %s", label, url, exc)
        return None
