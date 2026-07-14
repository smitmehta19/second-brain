"""Bucket taxonomy for the Second Brain — use-case based categorization axis.

A note may belong to ONE OR MORE buckets that describe HOW the user will
engage with the content (consumption intent), not WHAT the content is about.

Eight canonical buckets are hardcoded. Users may also create custom buckets
at runtime via the dashboard; these are persisted to data/custom_buckets.json.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

# ---------------------------------------------------------------------------
# Canonical bucket values (used for storage — always UPPER-CASE with hyphens)
# ---------------------------------------------------------------------------

BUCKETS: tuple[str, ...] = (
    "CAREER",
    "WATCH-LONG",
    "WATCH-SHORT",
    "MAKE",
    "SHOP",
    "READ",
    "INSPIRE",
    "DUMP",
)

# Display labels (Title Case)
BUCKET_LABELS: dict[str, str] = {
    "CAREER": "Career",
    "WATCH-LONG": "Watch Long",
    "WATCH-SHORT": "Watch Short",
    "MAKE": "Make",
    "SHOP": "Shop",
    "READ": "Read",
    "INSPIRE": "Inspire",
    "DUMP": "Dump",
}

# Canonical palette — frozen.  Custom buckets get assigned a color
# deterministically from CUSTOM_PALETTE via a stable hash of the bucket name.
BUCKET_COLORS: dict[str, str] = {
    "CAREER":      "#4FA8E0",
    "WATCH-LONG":  "#B280BA",
    "WATCH-SHORT": "#FF7847",
    "MAKE":        "#7CD66A",
    "SHOP":        "#FFB36A",
    "READ":        "#8FA8BB",
    "INSPIRE":     "#FFC857",
    "DUMP":        "#5A7185",
}

# Palette for user-created buckets — picked deterministically by name hash.
CUSTOM_PALETTE: tuple[str, ...] = (
    "#5FE7FF", "#E8A84A", "#5BD9A8", "#FF7847",
    "#A088F0", "#FFC857", "#7CD66A", "#FF8FB1",
    "#4FA8E0", "#B280BA",
)

# Validation: 1-16 chars, UPPER A-Z, 0-9, single hyphens between segments
_BUCKET_NAME_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")
_MAX_BUCKET_NAME_LEN = 16

_CUSTOM_BUCKETS_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "custom_buckets.json"
)

# Re-entrant safe mutation of the file; read+write happens on the API thread.
_FILE_LOCK = Lock()


# ---------------------------------------------------------------------------
# Custom-bucket loader / writer
# ---------------------------------------------------------------------------

def _load_custom_buckets() -> list[dict]:
    """Return the list of user-defined bucket records from disk.

    Each record: {"name": str, "color": str, "created_at": str}.
    Returns [] if the file is missing or unparseable.
    """
    try:
        if _CUSTOM_BUCKETS_FILE.exists():
            data = json.loads(_CUSTOM_BUCKETS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [b for b in data if isinstance(b, dict) and b.get("name")]
    except Exception:
        pass
    return []


def _save_custom_buckets(records: list[dict]) -> None:
    """Atomic write of the custom-buckets list."""
    _CUSTOM_BUCKETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CUSTOM_BUCKETS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(_CUSTOM_BUCKETS_FILE)


def get_custom_buckets() -> list[dict]:
    """Public accessor — snapshot of current custom buckets."""
    with _FILE_LOCK:
        return _load_custom_buckets()


def get_all_buckets() -> list[dict]:
    """Return canonical + custom buckets as a unified list of records.

    Each entry: {"name": str, "label": str, "color": str, "custom": bool}.
    Canonical buckets come first in their declared order, then custom buckets
    in their creation order.
    """
    out: list[dict] = []
    for name in BUCKETS:
        out.append({
            "name": name,
            "label": BUCKET_LABELS[name],
            "color": BUCKET_COLORS[name],
            "custom": False,
        })
    for rec in get_custom_buckets():
        name = rec.get("name", "")
        if not name:
            continue
        out.append({
            "name": name,
            "label": rec.get("label") or _to_label(name),
            "color": rec.get("color") or _color_for(name),
            "custom": True,
        })
    return out


def is_valid_bucket(s: str) -> bool:
    """Return True if *s* is a canonical OR a registered custom bucket."""
    if not s:
        return False
    if s in BUCKETS:
        return True
    return any(rec.get("name") == s for rec in get_custom_buckets())


def add_custom_bucket(name: str, label: str | None = None) -> dict:
    """Validate, persist, and return a new custom bucket record.

    Raises ValueError if the name is malformed or already exists (canonical or
    custom).  The newly created record is appended atomically.
    """
    name = (name or "").strip().upper()
    if not name:
        raise ValueError("Bucket name is required")
    if len(name) > _MAX_BUCKET_NAME_LEN:
        raise ValueError(
            f"Bucket name too long (max {_MAX_BUCKET_NAME_LEN} chars)"
        )
    if not _BUCKET_NAME_RE.fullmatch(name):
        raise ValueError(
            "Bucket name must be UPPER-CASE letters/digits, "
            "hyphen-separated (e.g. CAREER, WATCH-LONG)"
        )
    if name in BUCKETS:
        raise ValueError(f"'{name}' is a canonical bucket")
    with _FILE_LOCK:
        records = _load_custom_buckets()
        if any(r.get("name") == name for r in records):
            raise ValueError(f"Bucket '{name}' already exists")
        record = {
            "name": name,
            "label": label or _to_label(name),
            "color": _color_for(name),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        records.append(record)
        _save_custom_buckets(records)
    return record


def _to_label(name: str) -> str:
    """CAREER -> Career; WATCH-LONG -> Watch Long."""
    return " ".join(part.capitalize() for part in name.split("-"))


def _color_for(name: str) -> str:
    """Deterministic palette pick for a custom bucket."""
    # Simple sum-of-chars hash — stable, dependency-free.
    h = sum(ord(c) for c in name)
    return CUSTOM_PALETTE[h % len(CUSTOM_PALETTE)]


# ---------------------------------------------------------------------------
# Default bucket mapping — deterministic, no AI
# Used by: backfill script, AI fallback when AI returns invalid/missing bucket
# ---------------------------------------------------------------------------

def default_bucket(
    url_type: str | None,
    note_type: str | None,
    source_url: str | None,
) -> str:
    """Return the best bucket based on url_type, note_type, and source_url.

    Purely deterministic — no AI calls. Used as fallback and for backfill.
    Returns one of the canonical BUCKETS constants.
    """
    ut = (url_type or "").lower()
    nt = (note_type or "").lower()
    url = (source_url or "").lower()

    if ut == "ecommerce":
        return "SHOP"
    if ut == "recipe" or nt == "recipe":
        return "MAKE"
    if ut == "short_video" or "reel" in url or "tiktok.com" in url or "/shorts/" in url:
        return "WATCH-SHORT"
    if ut == "long_video" or "youtube.com/watch" in url or "youtu.be/" in url:
        return "WATCH-LONG"
    if ut == "job_posting" or nt == "person" or "linkedin.com/jobs" in url:
        return "CAREER"
    if ut in ("article", "blog_article", "news", "reference"):
        return "READ"
    return "DUMP"  # catchall — nothing else matched; user can recategorize in review
