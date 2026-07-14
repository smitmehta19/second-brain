"""API call & quota tracker — counts calls and tracks token/request limits.

Persists to SQLite so counts survive restarts.
Captures rate-limit headers from Groq and quota info from error messages.

Free tier limits (reset at midnight UTC):
  Gemini: 15 RPM, 1,500 RPD, 1M tokens/day
  Groq:   30 RPM, 14,400 RPD, 100K tokens/day
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DAILY_LIMITS: dict[str, dict[str, int]] = {
    "gemini": {"requests": 1500, "tokens": 1_000_000},
    "groq": {"requests": 14400, "tokens": 100_000},
}

# {provider: {date_str: count}}
_counts: dict[str, dict[str, int]] = {}
_db_path: str = ""

# Live quota snapshot from rate-limit headers / error messages
_quota: dict[str, dict[str, Any]] = {}


def init_tracker(db_path: str) -> None:
    """Call once at startup with the SQLite DB path."""
    global _db_path
    _db_path = db_path
    _load_today()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_today() -> None:
    """Restore today's counts from DB on startup."""
    if not _db_path:
        return
    import sqlite3
    try:
        conn = sqlite3.connect(_db_path)
        today = _today()
        cur = conn.execute(
            "SELECT provider, COUNT(*) FROM api_usage "
            "WHERE date(called_at) = ? GROUP BY provider",
            (today,),
        )
        for provider, count in cur.fetchall():
            _counts.setdefault(provider, {})[today] = count
        conn.close()
    except Exception as exc:
        logger.warning("Could not load credit counts from DB: %s", exc)


def record_call(provider: str) -> None:
    """Record one successful AI API call."""
    today = _today()
    bucket = _counts.setdefault(provider, {})
    bucket[today] = bucket.get(today, 0) + 1

    if not _db_path:
        return
    import sqlite3
    try:
        conn = sqlite3.connect(_db_path)
        conn.execute(
            "INSERT INTO api_usage (provider, called_at) VALUES (?, datetime('now'))",
            (provider,),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Could not persist call record: %s", exc)


def update_quota_from_headers(provider: str, headers: dict) -> None:
    """Extract rate-limit info from Groq/Gemini response headers."""
    today = _today()
    q = _quota.setdefault(provider, {"date": today})
    if q.get("date") != today:
        q.clear()
        q["date"] = today

    # Groq headers: x-ratelimit-remaining-tokens, x-ratelimit-remaining-requests
    for key, field in [
        ("x-ratelimit-remaining-tokens", "tokens_remaining"),
        ("x-ratelimit-limit-tokens", "tokens_limit"),
        ("x-ratelimit-remaining-requests", "requests_remaining"),
        ("x-ratelimit-limit-requests", "requests_limit"),
    ]:
        val = headers.get(key)
        if val is not None:
            try:
                q[field] = int(val)
            except (ValueError, TypeError):
                pass

    q["updated_at"] = datetime.now(timezone.utc).isoformat()


def update_quota_from_error(provider: str, error_text: str) -> None:
    """Parse quota info from rate-limit error messages.

    Groq errors include: "Limit 100000, Used 95944, Requested 10046"
    """
    today = _today()
    q = _quota.setdefault(provider, {"date": today})
    if q.get("date") != today:
        q.clear()
        q["date"] = today

    # Groq: "Limit 100000, Used 95944, Requested 10046"
    m = re.search(r"Limit\s+(\d+),\s*Used\s+(\d+),\s*Requested\s+(\d+)", error_text)
    if m:
        limit, used, requested = int(m.group(1)), int(m.group(2)), int(m.group(3))
        q["tokens_limit"] = limit
        q["tokens_used"] = used
        q["tokens_remaining"] = max(0, limit - used)
        q["last_requested"] = requested

    # Gemini: "You exceeded your current quota"
    if "exceeded" in error_text.lower() or "quota" in error_text.lower():
        q["exhausted"] = True

    q["updated_at"] = datetime.now(timezone.utc).isoformat()


def get_call_count(provider: str) -> int:
    """Return today's call count for any provider (e.g. 'ollama'), including
    ones not tracked in DAILY_LIMITS / get_usage() (which only covers
    providers with a known free-tier quota)."""
    return _counts.get(provider, {}).get(_today(), 0)


def get_usage() -> dict[str, Any]:
    """Return current usage statistics for all tracked providers."""
    today = _today()
    result: dict[str, Any] = {}

    for provider, limits in DAILY_LIMITS.items():
        req_limit = limits["requests"]
        token_limit = limits["tokens"]

        # Request count from our tracker
        reqs_used = _counts.get(provider, {}).get(today, 0)
        reqs_remaining = max(0, req_limit - reqs_used)

        # Token info from live quota (headers/errors) — more accurate
        q = _quota.get(provider, {})
        tokens_used = q.get("tokens_used")
        tokens_remaining = q.get("tokens_remaining")
        tokens_limit_live = q.get("tokens_limit", token_limit)

        if tokens_used is None:
            tokens_pct = None
        else:
            tokens_pct = round((tokens_used / tokens_limit_live) * 100, 1)

        exhausted = q.get("exhausted", False)
        if tokens_remaining is not None and tokens_remaining <= 0:
            exhausted = True

        status = "exhausted" if exhausted else (
            "low" if reqs_remaining < req_limit * 0.1 else "ok"
        )

        result[provider] = {
            "requests_used": reqs_used,
            "requests_limit": req_limit,
            "requests_remaining": reqs_remaining,
            "tokens_used": tokens_used,
            "tokens_limit": tokens_limit_live,
            "tokens_remaining": tokens_remaining,
            "tokens_pct": tokens_pct,
            "status": status,
            "updated_at": q.get("updated_at"),
            "resets": "midnight UTC (5:30 AM IST)",
        }

    return result
