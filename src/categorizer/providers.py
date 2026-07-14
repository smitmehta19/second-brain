"""Multi-provider AI backend with rate-limit aware fallback.

Flow: Gemini (free) → Groq (free) → Ollama (local, optional tier-3) →
keyword fallback + "come back tomorrow".
Detects 429 rate limits and daily quota exhaustion, auto-switches provider.
Tracks which providers are exhausted per day and resets at midnight UTC.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

try:
    from src.utils.credit_tracker import (
        record_call as _record_call,
        update_quota_from_headers as _update_headers,
        update_quota_from_error as _update_error,
    )
except ImportError:
    def _record_call(provider: str) -> None:  # type: ignore[misc]
        pass
    def _update_headers(provider: str, headers: dict) -> None:  # type: ignore[misc]
        pass
    def _update_error(provider: str, error_text: str) -> None:  # type: ignore[misc]
        pass


# ---------------------------------------------------------------------------
# Rate-limit tracking — resets daily at midnight UTC
# ---------------------------------------------------------------------------

class _RateLimitTracker:
    """Tracks which providers are exhausted today."""

    def __init__(self):
        self._exhausted: dict[str, str] = {}  # provider → date string
        self._today: str = ""

    def _check_reset(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today:
            self._exhausted.clear()
            self._today = today

    def mark_exhausted(self, provider: str):
        self._check_reset()
        self._exhausted[provider] = self._today
        logger.warning("Provider %s marked as exhausted for today (%s)", provider, self._today)

    def is_exhausted(self, provider: str) -> bool:
        self._check_reset()
        return provider in self._exhausted

    def all_exhausted(self, providers: list[str]) -> bool:
        self._check_reset()
        return all(p in self._exhausted for p in providers)

    @property
    def exhausted_list(self) -> list[str]:
        self._check_reset()
        return list(self._exhausted.keys())


_tracker = _RateLimitTracker()


def get_exhausted_slots() -> list[str]:
    """Return key-slot ids currently marked daily-exhausted (e.g. ['gemini_1'])."""
    return _tracker.exhausted_list


class AllProvidersExhaustedError(Exception):
    """Raised when all free AI providers have hit their daily limits."""
    pass


# ---------------------------------------------------------------------------
# All-providers-exhausted alerting — module-level callback, rate-limited to
# at most one alert per hour so a burst of captures doesn't spam Telegram.
# ---------------------------------------------------------------------------

_exhaustion_callback: Optional[Callable[[str], Awaitable[None]]] = None
_last_exhaustion_alert: float = 0.0
_EXHAUSTION_ALERT_COOLDOWN_SEC = 3600  # 1 hour


def register_exhaustion_callback(cb: Callable[[str], Awaitable[None]]) -> None:
    """Register an async callback invoked whenever all AI providers are exhausted.

    The callback receives the exhaustion message. Never called more than once
    per hour. Failures inside the callback are swallowed — call_ai() must
    still raise AllProvidersExhaustedError regardless of the callback outcome.
    """
    global _exhaustion_callback
    _exhaustion_callback = cb


async def _notify_exhaustion(message: str) -> None:
    """Invoke the registered exhaustion callback, guarded and rate-limited."""
    global _last_exhaustion_alert
    if _exhaustion_callback is None:
        return
    now = time.monotonic()
    if now - _last_exhaustion_alert < _EXHAUSTION_ALERT_COOLDOWN_SEC:
        logger.debug(
            "Exhaustion alert suppressed — last one sent %.0fs ago", now - _last_exhaustion_alert
        )
        return
    _last_exhaustion_alert = now
    try:
        await _exhaustion_callback(message)
    except Exception:
        logger.exception("Exhaustion callback failed")


# ---------------------------------------------------------------------------
# Rate-limit detection helpers
# ---------------------------------------------------------------------------

# Key slots that returned "invalid API key" — dead for the process lifetime,
# so the rotation stops wasting a request on them per categorization.
_invalid_keys: set[str] = set()


def _is_invalid_key(exc: Exception) -> bool:
    """True if the error means the API key itself is bad (401/invalid)."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
        return True
    msg = str(exc).lower()
    return "invalid api key" in msg or "api key not valid" in msg


def _is_rate_limit(exc: Exception) -> bool:
    """Check if an exception is a rate-limit / quota error."""
    msg = str(exc).lower()
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return True
        if exc.response.status_code == 403 and "quota" in msg:
            return True
    rate_keywords = ["rate limit", "quota", "too many requests", "resource exhausted",
                     "rate_limit", "tokens per minute", "requests per day"]
    return any(kw in msg for kw in rate_keywords)


# Per-minute 429s should be retried via backoff; only mark the key dead for the
# day on explicit daily markers. Bare "quota" alone is insufficient because
# Gemini's 15-RPM per-minute errors also include "quota" in the message.
def _is_daily_quota(exc: Exception) -> bool:
    """Check if the rate limit is a DAILY quota (not per-minute).

    Returns True only when the error body/message contains an explicit daily
    exhaustion marker; a bare 'quota' mention is NOT enough.
    """
    daily_keywords = [
        "per day", "perday", "requests per day", "tokens per day",
        "daily", "rpd", "tpd", "resource_exhausted",
        "prepayment credits are depleted",
    ]
    try:
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text.lower()
            return any(kw in body for kw in daily_keywords)
    except Exception:
        pass
    msg = str(exc).lower()
    return any(kw in msg for kw in daily_keywords)


def _get_rate_limit_detail(exc: Exception) -> str:
    """Extract useful detail from a rate-limit error for logging."""
    try:
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text[:300]
            return body
    except Exception:
        pass
    return str(exc)[:200]


def _is_payload_too_large(exc: Exception) -> bool:
    """Check if error is a 413 Payload Too Large."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 413
    return False


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

async def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from AI response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Gemini — FREE: 15 RPM, 1M tokens/day, 1500 req/day
# ---------------------------------------------------------------------------

async def call_gemini(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = "gemini-2.0-flash",
) -> dict[str, Any]:
    """Call Google Gemini API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        _update_headers("gemini", dict(resp.headers))
        data = resp.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return await _extract_json(text)


# ---------------------------------------------------------------------------
# Groq — FREE: 30 RPM, 14,400 req/day
# ---------------------------------------------------------------------------

async def call_groq(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = "llama-3.3-70b-versatile",
) -> dict[str, Any]:
    """Call Groq API. Auto-truncates on 413 Payload Too Large."""
    url = "https://api.groq.com/openai/v1/chat/completions"

    # Groq has stricter payload limits — truncate user prompt if needed
    max_user_len = 12000
    truncated_prompt = user_prompt
    if len(user_prompt) > max_user_len:
        truncated_prompt = user_prompt[:max_user_len] + "\n\n[content truncated for processing]"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": truncated_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,  # free tier TPM limit is 12K; 8192 alone burned 68% leaving no room for input
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        # On 413, retry with aggressively truncated content
        if resp.status_code == 413:
            logger.warning("Groq 413 — retrying with truncated content (6000 chars)")
            short_prompt = user_prompt[:6000] + "\n\n[content truncated for processing]"
            payload["messages"][1]["content"] = short_prompt
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
        resp.raise_for_status()
        _update_headers("groq", dict(resp.headers))
        data = resp.json()

    text = data["choices"][0]["message"]["content"]
    return await _extract_json(text)


# ---------------------------------------------------------------------------
# Ollama — FREE: local, no API key, no daily quota. Tier-3 fallback only,
# used after both Gemini and Groq are exhausted, and only when explicitly
# enabled via settings.enable_ollama_fallback.
# ---------------------------------------------------------------------------

async def call_ollama(
    system_prompt: str,
    user_prompt: str,
    base_url: str,
    model: str,
    expect_json: bool = True,
) -> dict[str, Any]:
    """Call a local Ollama /api/chat endpoint. Raises on connection error or non-200.

    Ollama has no rate limits or daily quota to track — callers should treat
    any exception here as fatal for this attempt (no retry/backoff loop).
    """
    url = f"{base_url.rstrip('/')}/api/chat"

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    if expect_json:
        payload["format"] = "json"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    text = (data.get("message") or {}).get("content", "")
    return await _extract_json(text)


# ---------------------------------------------------------------------------
# API key health checks — cheap liveness probes using free list-models
# endpoints (no tokens consumed). Used at bot startup and can be called
# on-demand (e.g. from a dashboard/health endpoint).
# ---------------------------------------------------------------------------

async def _check_gemini_key(api_key: str) -> str:
    """Liveness-check one Gemini key. Returns ok/invalid/quota/unreachable."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return "ok"
        if resp.status_code == 429:
            return "quota"
        if resp.status_code in (400, 401, 403):
            return "invalid"
        return "unreachable"
    except Exception as exc:
        logger.debug("Gemini key health check failed: %s", exc)
        return "unreachable"


async def _check_groq_key(api_key: str) -> str:
    """Liveness-check one Groq key. Returns ok/invalid/quota/unreachable."""
    url = "https://api.groq.com/openai/v1/models"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        if resp.status_code == 200:
            return "ok"
        if resp.status_code == 429:
            return "quota"
        if resp.status_code in (400, 401, 403):
            return "invalid"
        return "unreachable"
    except Exception as exc:
        logger.debug("Groq key health check failed: %s", exc)
        return "unreachable"


async def validate_api_keys(settings: Any) -> dict[str, list[dict[str, str]]]:
    """Cheap liveness check for every configured Gemini/Groq key.

    Runs all checks concurrently via asyncio.gather (10s timeout each, so the
    whole check takes ~10s regardless of key count). Uses the free
    list-models endpoints — no quota/tokens consumed.

    Returns:
        {"gemini": [{"key_slot": "gemini_0", "status": "ok"}, ...],
         "groq":   [{"key_slot": "groq_0", "status": "invalid"}, ...]}
    """
    gemini_keys = getattr(settings, "all_gemini_keys", None) or (
        [settings.gemini_api_key] if getattr(settings, "gemini_api_key", None) else []
    )
    groq_keys = getattr(settings, "all_groq_keys", None) or (
        [settings.groq_api_key] if getattr(settings, "groq_api_key", None) else []
    )

    gemini_statuses, groq_statuses = await asyncio.gather(
        asyncio.gather(*[_check_gemini_key(k) for k in gemini_keys]),
        asyncio.gather(*[_check_groq_key(k) for k in groq_keys]),
    )

    result: dict[str, list[dict[str, str]]] = {"gemini": [], "groq": []}
    non_ok = 0

    for i, status in enumerate(gemini_statuses):
        slot = f"gemini_{i}"
        result["gemini"].append({"key_slot": slot, "status": status})
        if status != "ok":
            non_ok += 1
            logger.warning("API key health check: %s is %s", slot, status)

    for i, status in enumerate(groq_statuses):
        slot = f"groq_{i}"
        result["groq"].append({"key_slot": slot, "status": status})
        if status != "ok":
            non_ok += 1
            logger.warning("API key health check: %s is %s", slot, status)

    total = len(gemini_statuses) + len(groq_statuses)
    logger.info(
        "API key health check: %d/%d keys ok (%d gemini, %d groq configured)",
        total - non_ok, total, len(gemini_statuses), len(groq_statuses),
    )
    return result


# ---------------------------------------------------------------------------
# Main call_ai with rate-limit aware fallback
# ---------------------------------------------------------------------------

async def call_ai(
    system_prompt: str,
    user_prompt: str,
    settings: Any,
    preferred_provider: Optional[str] = None,
) -> dict[str, Any]:
    """Call AI with key-rotation aware fallback: Gemini → Groq → raise exhausted.

    Supports multiple API keys per provider (GEMINI_API_KEYS / GROQ_API_KEYS,
    comma-separated). When a key hits its daily quota, automatically tries the
    next available key before falling through to the next provider.
    """
    chain: list[str] = []
    if preferred_provider and preferred_provider in ("gemini", "groq"):
        chain.append(preferred_provider)
    for name in ["gemini", "groq"]:
        if name not in chain:
            chain.append(name)

    def _get_keys(provider: str) -> list[str]:
        if provider == "gemini":
            prop = getattr(settings, "all_gemini_keys", None)
            return prop if prop else ([settings.gemini_api_key] if getattr(settings, "gemini_api_key", None) else [])
        if provider == "groq":
            prop = getattr(settings, "all_groq_keys", None)
            return prop if prop else ([settings.groq_api_key] if getattr(settings, "groq_api_key", None) else [])
        return []

    configured = [name for name in chain if _get_keys(name)]

    if not configured:
        raise RuntimeError(
            "No AI provider configured. Set GEMINI_API_KEY or GROQ_API_KEY in .env. "
            "Both are free: Gemini at https://aistudio.google.com/apikey, "
            "Groq at https://console.groq.com/keys"
        )

    # Track exhaustion per key-slot: "gemini_0", "gemini_1", "groq_0", …
    all_key_ids = [f"{name}_{i}" for name in configured for i in range(len(_get_keys(name)))]
    if _tracker.all_exhausted(all_key_ids):
        raise AllProvidersExhaustedError(
            "All AI providers have hit their daily free limits. "
            f"Exhausted today: {', '.join(_tracker.exhausted_list)}. "
            "Limits reset at midnight UTC. Come back tomorrow!"
        )

    errors: list[str] = []
    max_attempts = 4
    backoff_schedule = [30, 60, 60, 60]

    for name in configured:
        keys = _get_keys(name)
        model = getattr(settings, f"{name}_model", {"gemini": "gemini-2.0-flash", "groq": "llama-3.3-70b-versatile"}[name])

        for key_idx, api_key in enumerate(keys):
            key_id = f"{name}_{key_idx}"
            if key_id in _invalid_keys:
                logger.debug("Skipping %s — key marked invalid", key_id)
                continue
            if _tracker.is_exhausted(key_id):
                logger.info("Skipping %s key %d/%d — exhausted for today", name, key_idx + 1, len(keys))
                continue

            label = name if len(keys) == 1 else f"{name}[key {key_idx + 1}/{len(keys)}]"

            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info("Trying %s (attempt %d/%d)", label, attempt, max_attempts)

                    if name == "gemini":
                        result = await call_gemini(system_prompt, user_prompt, api_key=api_key, model=model)
                    elif name == "groq":
                        result = await call_groq(system_prompt, user_prompt, api_key=api_key, model=model)
                    else:
                        break

                    logger.info("%s succeeded on attempt %d", label, attempt)
                    _record_call(name)
                    result["_provider"] = name
                    return result

                except Exception as exc:
                    detail = _get_rate_limit_detail(exc)
                    _update_error(name, str(exc) + " " + detail)

                    if _is_invalid_key(exc):
                        _invalid_keys.add(key_id)
                        logger.warning(
                            "%s rejected as INVALID — disabling this key slot for "
                            "the rest of the process. Fix or remove it in .env.",
                            label,
                        )
                        errors.append(f"{label}: invalid API key")
                        break

                    if not _is_rate_limit(exc):
                        logger.warning("%s failed (non-rate-limit): %s", label, exc)
                        errors.append(f"{label}: {exc}")
                        break

                    if _is_daily_quota(exc):
                        _tracker.mark_exhausted(key_id)
                        logger.warning("%s hit DAILY quota — trying next key. Detail: %s", label, detail)
                        errors.append(f"{label}: daily quota exhausted")
                        break

                    # Per-minute rate limit — retry with backoff
                    if attempt < max_attempts:
                        wait = backoff_schedule[attempt - 1]
                        logger.warning(
                            "%s rate-limited (attempt %d/%d) — waiting %ds. Detail: %s",
                            label, attempt, max_attempts, wait, detail,
                        )
                        await asyncio.sleep(wait)
                        continue

                    _tracker.mark_exhausted(key_id)
                    logger.warning("%s rate-limited after %d attempts — exhausting key.", label, max_attempts)
                    errors.append(f"{label}: rate-limited after {max_attempts} retries")
                    break

    # Tier-3: local Ollama fallback. Only reached after Gemini AND Groq have
    # both been fully attempted (all keys exhausted or errored) above. Never
    # tried when the feature flag is off — Ollama requires a local install
    # most deployments won't have, so it must stay strictly opt-in.
    if getattr(settings, "enable_ollama_fallback", False):
        ollama_model = getattr(settings, "ollama_model", "llama3.2")
        ollama_url = getattr(settings, "ollama_url", "http://localhost:11434")
        try:
            logger.info(
                "Gemini/Groq exhausted — trying Ollama fallback (%s @ %s)",
                ollama_model, ollama_url,
            )
            result = await call_ollama(
                system_prompt,
                user_prompt,
                base_url=ollama_url,
                model=ollama_model,
            )
            logger.info("Ollama fallback succeeded")
            _record_call("ollama")
            result["_provider"] = "ollama"
            return result
        except Exception as exc:
            logger.warning("Ollama fallback failed (%s): %s", type(exc).__name__, exc)
            errors.append(f"ollama: {exc}")
            exhausted_msg = (
                "Gemini and Groq exhausted for today, and the Ollama fallback "
                f"also failed ({exc}). Content saved with keyword-based "
                "categorization. AI categorization auto-resumes at midnight UTC "
                "(5:30 AM IST)."
            )
            await _notify_exhaustion(exhausted_msg)
            raise AllProvidersExhaustedError(exhausted_msg) from exc

    if _tracker.all_exhausted(all_key_ids):
        exhausted_msg = (
            "All AI providers exhausted for today. "
            "Content saved with keyword-based categorization. "
            "AI categorization auto-resumes at midnight UTC (5:30 AM IST)."
        )
        await _notify_exhaustion(exhausted_msg)
        raise AllProvidersExhaustedError(exhausted_msg)

    raise RuntimeError(f"All AI providers failed: {'; '.join(errors)}")
