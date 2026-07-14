"""SQLite persistence layer for the Second Brain pipeline.

Uses ``aiosqlite`` for fully async database access.  All public functions
operate on a module-level connection that is created by :func:`init_db`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

from src.models.schemas import ProcessingStatus, RawCapture, StoredNote
from src.utils.helpers import now_iso

logger = logging.getLogger(__name__)

# Module-level connection — initialised by ``init_db``.
_db: Optional[aiosqlite.Connection] = None

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS captures (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    content_type TEXT NOT NULL,
    text        TEXT,
    url         TEXT,
    file_path   TEXT,
    caption     TEXT,
    sender      TEXT DEFAULT 'self',
    source_chat TEXT DEFAULT 'telegram',
    status      TEXT DEFAULT 'queued',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notes (
    id             TEXT PRIMARY KEY,
    capture_id     TEXT REFERENCES captures(id),
    title          TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    notion_page_id TEXT,
    note_type      TEXT NOT NULL,
    domains        TEXT NOT NULL,
    tags           TEXT NOT NULL,
    source_url     TEXT,
    summary        TEXT,
    key_takeaways  TEXT,
    quality_score  INTEGER DEFAULT 3,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processing_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT REFERENCES captures(id),
    status     TEXT NOT NULL,
    message    TEXT,
    timestamp  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    provider   TEXT NOT NULL,
    called_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_usage_date ON api_usage (date(called_at), provider);
"""

_MIGRATION_SQL = """\
ALTER TABLE notes ADD COLUMN summary TEXT;
ALTER TABLE notes ADD COLUMN key_takeaways TEXT;
ALTER TABLE notes ADD COLUMN url_content_type TEXT DEFAULT 'unknown';
ALTER TABLE notes ADD COLUMN why_keep TEXT DEFAULT '';
ALTER TABLE notes ADD COLUMN open_loops TEXT DEFAULT '[]';
ALTER TABLE notes ADD COLUMN structured_data TEXT DEFAULT '{}';
ALTER TABLE notes ADD COLUMN personal_relevance INTEGER DEFAULT 3;
ALTER TABLE notes ADD COLUMN priority TEXT DEFAULT 'medium';
ALTER TABLE notes ADD COLUMN action_items TEXT DEFAULT '[]';
ALTER TABLE notes ADD COLUMN reviewed_at TEXT;
ALTER TABLE notes ADD COLUMN review_action TEXT;
ALTER TABLE notes ADD COLUMN bucket TEXT;
ALTER TABLE notes ADD COLUMN buckets TEXT DEFAULT '[]';
ALTER TABLE notes ADD COLUMN content_json TEXT;
ALTER TABLE notes ADD COLUMN extraction_confidence REAL;
"""


def _get_db() -> aiosqlite.Connection:
    """Return the active database connection or raise."""
    if _db is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _db


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def init_db(db_path: str) -> None:
    """Create the database file (if needed) and ensure all tables exist."""
    global _db  # noqa: PLW0603

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(path))
    _db.row_factory = aiosqlite.Row
    await _db.executescript(_SCHEMA_SQL)
    await _db.commit()

    # Run migrations (ALTER TABLE is safe to repeat — errors on existing cols are OK)
    for line in _MIGRATION_SQL.strip().splitlines():
        line = line.strip()
        if line:
            try:
                await _db.execute(line)
            except aiosqlite.OperationalError as exc:
                if "duplicate column" in str(exc).lower():
                    pass  # Column already exists — expected
                else:
                    raise
    await _db.commit()

    logger.info("Database initialised at %s", path)


async def close_db() -> None:
    """Close the database connection gracefully."""
    global _db  # noqa: PLW0603
    if _db is not None:
        await _db.close()
        _db = None
        logger.info("Database connection closed")


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------


async def save_capture(capture: RawCapture) -> None:
    """Insert a new raw capture row."""
    db = _get_db()
    await db.execute(
        """
        INSERT INTO captures (id, timestamp, content_type, text, url, file_path,
                              caption, sender, source_chat, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            capture.id,
            capture.timestamp.isoformat(),
            capture.content_type.value,
            capture.text,
            capture.url,
            capture.file_path,
            capture.caption,
            capture.sender,
            capture.source_chat,
            ProcessingStatus.QUEUED.value,
        ),
    )
    await db.commit()
    logger.debug("Saved capture %s", capture.id)


async def update_capture_status(
    capture_id: str,
    status: ProcessingStatus,
    message: str | None = None,
) -> None:
    """Update a capture's status and append a processing-log entry."""
    db = _get_db()
    await db.execute(
        "UPDATE captures SET status = ? WHERE id = ?",
        (status.value, capture_id),
    )
    await db.execute(
        "INSERT INTO processing_log (capture_id, status, message, timestamp) VALUES (?, ?, ?, ?)",
        (capture_id, status.value, message, now_iso()),
    )
    await db.commit()


async def get_capture_by_id(capture_id: str) -> dict | None:
    """Fetch a single capture row as a dict, or ``None``."""
    db = _get_db()
    cursor = await db.execute("SELECT * FROM captures WHERE id = ?", (capture_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_recent_captures(limit: int = 20) -> list[dict]:
    """Return the most recent captures, newest first."""
    db = _get_db()
    cursor = await db.execute(
        "SELECT * FROM captures ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def get_failed_captures() -> list[dict]:
    """Return all captures with status ``FAILED``."""
    db = _get_db()
    cursor = await db.execute(
        "SELECT * FROM captures WHERE status = ? ORDER BY created_at DESC",
        (ProcessingStatus.FAILED.value,),
    )
    return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# URL dedup
# ---------------------------------------------------------------------------


async def url_already_exists(url: str) -> dict | None:
    """Check if a URL has already been captured and processed successfully.

    Checks both captures.url and notes.source_url to catch all variants.
    Returns the existing note dict if found, None otherwise.
    """
    db = _get_db()
    clean = url.rstrip("/")
    variants = (url, clean, clean + "/")
    cursor = await db.execute(
        """
        SELECT n.id, n.title, n.domains, n.created_at
        FROM notes n
        JOIN captures c ON n.capture_id = c.id
        WHERE c.url IN (?, ?, ?)
           OR n.source_url IN (?, ?, ?)
        LIMIT 1
        """,
        (*variants, *variants),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


async def save_note(
    note: StoredNote,
    capture_id: str,
    *,
    url_content_type: str = "unknown",
    why_keep: str = "",
    open_loops: list | None = None,
    structured_data: dict | None = None,
    personal_relevance: int = 3,
    priority: str = "medium",
    action_items: list | None = None,
    bucket: str | None = None,
    content_json: str | None = None,
    extraction_confidence: float | None = None,
) -> None:
    """Insert a stored-note row linked to its capture.

    ``content_json`` is the full serialized CategorizedContent — kept so the
    Notion page can be built later (publish-on-keep) without re-extracting.
    """
    db = _get_db()
    await db.execute(
        """
        INSERT INTO notes (id, capture_id, title, file_path, notion_page_id,
                           note_type, domains, tags, source_url, summary,
                           key_takeaways, quality_score, created_at,
                           url_content_type, why_keep, open_loops,
                           structured_data, personal_relevance, priority,
                           action_items, bucket, content_json, extraction_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note.id,
            capture_id,
            note.title,
            note.file_path,
            note.notion_page_id,
            note.note_type.value,
            json.dumps(note.domains),
            json.dumps(note.tags),
            note.source_url,
            note.summary,
            json.dumps(note.key_takeaways),
            note.quality_score,
            note.created_at.isoformat(),
            url_content_type,
            why_keep,
            json.dumps(open_loops or []),
            json.dumps(structured_data or {}),
            personal_relevance,
            priority,
            json.dumps(action_items or []),
            bucket or note.bucket,
            content_json,
            extraction_confidence,
        ),
    )
    await db.commit()
    logger.debug("Saved note %s for capture %s", note.id, capture_id)


async def delete_note(note_id: str) -> bool:
    """Delete a note by ID. Returns True if a row was deleted.

    Also removes the associated capture and processing_log rows so no
    orphans are left behind.
    """
    db = _get_db()

    # Fetch capture_id from the note row BEFORE deleting it.
    cursor = await db.execute(
        "SELECT capture_id FROM notes WHERE id = ?", (note_id,)
    )
    note_row = await cursor.fetchone()

    cursor = await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    await db.commit()

    if cursor.rowcount > 0:
        logger.info("Deleted note %s", note_id)
        if note_row and note_row["capture_id"]:
            capture_id = note_row["capture_id"]
            await db.execute(
                "DELETE FROM processing_log WHERE capture_id = ?", (capture_id,)
            )
            await db.execute(
                "DELETE FROM captures WHERE id = ?", (capture_id,)
            )
            await db.commit()
        return True
    return False


async def get_note_by_capture_id(capture_id: str) -> dict | None:
    """Fetch the note linked to a capture, or None."""
    db = _get_db()
    cursor = await db.execute(
        "SELECT * FROM notes WHERE capture_id = ?", (capture_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_note_keep_capture(capture_id: str) -> dict | None:
    """Delete the note for a capture but keep the capture row.

    Returns the deleted note's data (for cleanup of Obsidian/Notion) or None.
    """
    db = _get_db()
    cursor = await db.execute(
        "SELECT * FROM notes WHERE capture_id = ?", (capture_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    old_note = dict(row)
    await db.execute("DELETE FROM notes WHERE capture_id = ?", (capture_id,))
    await db.commit()
    logger.info("Deleted note %s for reprocess (capture %s kept)", old_note["id"], capture_id)
    return old_note


async def forget_url(url: str) -> bool:
    """Delete the note AND capture for a URL so it can be re-submitted.

    Cleans both tables so the URL dedup check won't block re-processing.
    Returns True if anything was deleted.
    """
    db = _get_db()
    clean = url.rstrip("/")
    variants = (url, clean, clean + "/")

    # Find the note + capture
    cursor = await db.execute(
        """
        SELECT n.id AS note_id, c.id AS capture_id
        FROM notes n
        JOIN captures c ON n.capture_id = c.id
        WHERE c.url IN (?, ?, ?)
           OR n.source_url IN (?, ?, ?)
        """,
        (*variants, *variants),
    )
    rows = await cursor.fetchall()

    if not rows:
        # Maybe only a capture exists (no note yet)
        cursor = await db.execute(
            "SELECT id FROM captures WHERE url IN (?, ?, ?)", variants,
        )
        cap_rows = await cursor.fetchall()
        for row in cap_rows:
            await db.execute("DELETE FROM captures WHERE id = ?", (row["id"],))
            await db.execute("DELETE FROM processing_log WHERE capture_id = ?", (row["id"],))
        await db.commit()
        return len(cap_rows) > 0

    for row in rows:
        await db.execute("DELETE FROM notes WHERE id = ?", (row["note_id"],))
        await db.execute("DELETE FROM captures WHERE id = ?", (row["capture_id"],))
        await db.execute("DELETE FROM processing_log WHERE capture_id = ?", (row["capture_id"],))

    await db.commit()
    logger.info("Forgot URL %s (%d notes deleted)", url, len(rows))
    return True


async def update_note_bucket(note_id: str, bucket: str) -> bool:
    """Update the (legacy single) bucket column. Kept for back-compat.

    Prefer ``update_note_buckets`` for new code.
    """
    db = _get_db()
    cursor = await db.execute(
        "UPDATE notes SET bucket = ?, buckets = ? WHERE id = ?",
        (bucket, json.dumps([bucket]), note_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_note_buckets(note_id: str, buckets: list[str]) -> bool:
    """Update both the JSON-encoded ``buckets`` array and the legacy
    single-value ``bucket`` column (set to ``buckets[0]``).

    Returns True iff a row was updated.
    """
    if not buckets:
        return False
    db = _get_db()
    cursor = await db.execute(
        "UPDATE notes SET buckets = ?, bucket = ? WHERE id = ?",
        (json.dumps(buckets), buckets[0], note_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def search_notes(query: str) -> list[dict]:
    """Full-text search across title, domains, and tags columns."""
    db = _get_db()
    like = f"%{query}%"
    cursor = await db.execute(
        """
        SELECT * FROM notes
        WHERE title LIKE ? OR domains LIKE ? OR tags LIKE ?
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (like, like, like),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def get_notes_by_ids(note_ids: list[str]) -> list[dict]:
    """Fetch full note rows by IDs, preserving order."""
    if not note_ids:
        return []
    db = _get_db()
    placeholders = ",".join("?" for _ in note_ids)
    cursor = await db.execute(
        f"SELECT * FROM notes WHERE id IN ({placeholders})",
        note_ids,
    )
    rows = {r["id"]: dict(r) for r in await cursor.fetchall()}
    return [rows[nid] for nid in note_ids if nid in rows]


async def search_notes_rich(query: str, limit: int = 8) -> list[dict]:
    """Search notes with full context — summary, key_takeaways, structured_data."""
    db = _get_db()
    like = f"%{query}%"
    cursor = await db.execute(
        """
        SELECT id, title, summary, key_takeaways, structured_data,
               domains, tags, note_type, source_url, quality_score,
               why_keep, action_items, created_at
        FROM notes
        WHERE title LIKE ? OR domains LIKE ? OR tags LIKE ?
              OR summary LIKE ? OR key_takeaways LIKE ?
        ORDER BY quality_score DESC, created_at DESC
        LIMIT ?
        """,
        (like, like, like, like, like, limit),
    )
    return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Dashboard read/write API (SQLite is the source of truth; docs/notes.json is
# a generated cache — see src/search/export_json.py)
# ---------------------------------------------------------------------------


def _safe_json_load(val, default):
    """Parse a JSON column value, returning default on any failure."""
    if not val:
        return default
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def row_to_dashboard_note(row) -> dict:
    """Shape a notes-table row (joined with captures.text) into the dict the
    dashboard consumes — identical to the docs/notes.json entry format."""
    keys = row.keys()
    sd = _safe_json_load(row["structured_data"], {})
    images = sd.get("images", []) if isinstance(sd, dict) else []
    buckets = _safe_json_load(row["buckets"] if "buckets" in keys else None, [])
    if not buckets and row["bucket"]:
        buckets = [row["bucket"]]
    return {
        "id": row["id"],
        "title": row["title"],
        "summary": row["summary"] or "",
        "domains": _safe_json_load(row["domains"], []),
        "tags": _safe_json_load(row["tags"], []),
        "note_type": row["note_type"],
        "source_url": row["source_url"] or "",
        "quality_score": row["quality_score"] or 3,
        "created_at": row["created_at"] or "",
        "key_takeaways": _safe_json_load(row["key_takeaways"], []),
        "content_preview": ((row["text"] if "text" in keys else "") or "")[:500],
        "notion_page_id": row["notion_page_id"] or "",
        "url_content_type": row["url_content_type"] or "unknown",
        "why_keep": row["why_keep"] or "",
        "open_loops": _safe_json_load(row["open_loops"], []),
        "structured_data": sd,
        "images": images,
        "bucket": row["bucket"] or "",
        "buckets": buckets,
        "personal_relevance": row["personal_relevance"] or 3,
        "priority": row["priority"] or "medium",
        "action_items": _safe_json_load(row["action_items"], []),
        "reviewed_at": row["reviewed_at"] or "",
        "review_action": row["review_action"] or "",
        "extraction_confidence": (
            row["extraction_confidence"] if "extraction_confidence" in keys else None
        ),
    }


_DASHBOARD_SELECT = """
    SELECT n.*, c.text
    FROM notes n LEFT JOIN captures c ON n.capture_id = c.id
"""


async def fetch_dashboard_notes() -> list[dict]:
    """All notes shaped for the dashboard, newest first."""
    db = _get_db()
    cursor = await db.execute(_DASHBOARD_SELECT + " ORDER BY n.created_at DESC")
    return [row_to_dashboard_note(r) for r in await cursor.fetchall()]


async def fetch_review_queue(days: int = 7) -> list[dict]:
    """Unreviewed notes from the last *days* days, oldest first."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = _get_db()
    cursor = await db.execute(
        _DASHBOARD_SELECT
        + " WHERE (n.reviewed_at IS NULL OR n.reviewed_at = '')"
        + " AND n.created_at >= ? ORDER BY n.created_at ASC",
        (cutoff,),
    )
    return [row_to_dashboard_note(r) for r in await cursor.fetchall()]


async def get_note_row(note_id: str) -> dict | None:
    """Full raw note row (including content_json) as a dict, or None."""
    db = _get_db()
    cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_note_review(note_id: str, buckets: list[str], reviewed_at: str) -> bool:
    """Mark a note reviewed (keep) with its bucket assignment."""
    if not buckets:
        return False
    db = _get_db()
    cursor = await db.execute(
        "UPDATE notes SET reviewed_at = ?, review_action = 'keep', "
        "buckets = ?, bucket = ? WHERE id = ?",
        (reviewed_at, json.dumps(buckets), buckets[0], note_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def set_note_notion_page(note_id: str, page_id: str) -> bool:
    """Record the Notion page id after a deferred (publish-on-keep) create."""
    db = _get_db()
    cursor = await db.execute(
        "UPDATE notes SET notion_page_id = ? WHERE id = ?", (page_id, note_id)
    )
    await db.commit()
    return cursor.rowcount > 0


_EDITABLE_NOTE_FIELDS = {"title", "summary", "why_keep"}
_EDITABLE_JSON_FIELDS = {"tags", "key_takeaways"}


async def update_note_fields(note_id: str, fields: dict) -> bool:
    """Update editable note fields (title/summary/why_keep/tags/key_takeaways).

    Unknown fields are rejected with ValueError so the API layer can 400.
    """
    sets: list[str] = []
    params: list = []
    for key, value in fields.items():
        if key in _EDITABLE_NOTE_FIELDS:
            if not isinstance(value, str):
                raise ValueError(f"'{key}' must be a string")
            sets.append(f"{key} = ?")
            params.append(value)
        elif key in _EDITABLE_JSON_FIELDS:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"'{key}' must be a list of strings")
            sets.append(f"{key} = ?")
            params.append(json.dumps(value))
        else:
            raise ValueError(f"'{key}' is not editable")
    if not sets:
        return False
    db = _get_db()
    cursor = await db.execute(
        f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", (*params, note_id)
    )
    await db.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def get_stats() -> dict:
    """Return aggregate counts by content type, domain, and status."""
    db = _get_db()

    # Counts by content type
    cursor = await db.execute(
        "SELECT content_type, COUNT(*) as cnt FROM captures GROUP BY content_type"
    )
    by_type = {row["content_type"]: row["cnt"] for row in await cursor.fetchall()}

    # Counts by status
    cursor = await db.execute(
        "SELECT status, COUNT(*) as cnt FROM captures GROUP BY status"
    )
    by_status = {row["status"]: row["cnt"] for row in await cursor.fetchall()}

    # Counts by domain (notes table, JSON array stored as text)
    cursor = await db.execute("SELECT domains FROM notes")
    domain_counts: dict[str, int] = {}
    for row in await cursor.fetchall():
        for domain in json.loads(row["domains"]):
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    # Totals
    cursor = await db.execute("SELECT COUNT(*) as cnt FROM captures")
    total_captures = (await cursor.fetchone())["cnt"]

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM notes")
    total_notes = (await cursor.fetchone())["cnt"]

    return {
        "total_captures": total_captures,
        "total_notes": total_notes,
        "by_type": by_type,
        "by_status": by_status,
        "by_domain": domain_counts,
    }
