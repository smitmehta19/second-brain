"""Shared utility functions for the Second Brain pipeline."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone


def sanitize_filename(name: str) -> str:
    """Remove characters that are invalid in file names across platforms.

    Replaces sequences of invalid characters with a single hyphen and strips
    leading/trailing whitespace and hyphens.
    """
    # Remove characters invalid on Windows and most filesystems
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    # Collapse multiple hyphens / spaces
    cleaned = re.sub(r"[-\s]+", "-", cleaned)
    # Strip leading/trailing hyphens and whitespace
    cleaned = cleaned.strip("- ")
    # Limit length to 200 characters to avoid path-length issues
    return cleaned[:200] if cleaned else "untitled"


def truncate(text: str, max_len: int = 4000) -> str:
    """Truncate *text* to *max_len* characters, appending an ellipsis if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


_URL_PATTERN = re.compile(
    r"https?://"
    r"(?:[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%])+",
    re.ASCII,
)


def extract_urls(text: str) -> list[str]:
    """Return all HTTP/HTTPS URLs found in *text*."""
    return _URL_PATTERN.findall(text)


def now_iso() -> str:
    """Return the current UTC datetime as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def generate_id() -> str:
    """Generate a short (12-character) hex identifier."""
    return uuid.uuid4().hex[:12]
