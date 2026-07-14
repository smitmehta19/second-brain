"""Unified storage integrations for the Second Brain pipeline."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from src.config.settings import Settings
from src.integrations.notion_sync import save_to_notion, update_notion_page
from src.models.schemas import CategorizedContent, ProcessingStatus, StoredNote

logger = logging.getLogger(__name__)

__all__ = [
    "store_content",
    "save_to_notion",
    "publish_note_to_notion",
]


def _build_stored_note(content: CategorizedContent) -> StoredNote:
    """Build a StoredNote from categorized content (no filesystem needed)."""
    return StoredNote(
        id=uuid.uuid4().hex[:12],
        title=content.extracted.title,
        file_path="",
        note_type=content.note_type,
        domains=content.domains,
        tags=content.tags,
        source_url=content.extracted.url,
        summary=content.extracted.summary,
        key_takeaways=content.key_takeaways,
        quality_score=content.quality_score,
        bucket=content.bucket,
        status=ProcessingStatus.DONE,
    )


async def store_content(
    content: CategorizedContent,
    settings: Settings,
    *,
    reprocess_ctx: dict[str, Any] | None = None,
) -> StoredNote:
    """Build the StoredNote and (unless publish-on-keep defers it) sync to Notion.

    With ``settings.publish_on_keep`` enabled, new captures are NOT pushed to
    Notion here — they wait in the dashboard Review queue and are published by
    :func:`publish_note_to_notion` when the user keeps them. A reprocess of a
    note that already has a Notion page still updates that page in-place, so
    already-published notes never go stale.

    When *reprocess_ctx* is provided (contains old_notion_page_id),
    the existing Notion page is updated in-place rather than duplicated.
    """
    old_notion_id = (reprocess_ctx or {}).get("old_notion_page_id")

    stored_note = _build_stored_note(content)

    if settings.publish_on_keep and not old_notion_id:
        logger.debug(
            "publish_on_keep: deferring Notion page for '%s' until Review-Keep",
            content.extracted.title,
        )
        return stored_note

    # Sync to Notion
    try:
        notion_page_id: Optional[str] = None
        if old_notion_id:
            notion_page_id = await update_notion_page(old_notion_id, content, settings)
            if not notion_page_id:
                logger.info("Notion update failed — creating new page instead")
        if not notion_page_id:
            notion_page_id = await save_to_notion(content, settings)
        if notion_page_id:
            stored_note.notion_page_id = notion_page_id
    except Exception:
        logger.exception("Notion sync failed — note saved to DB only")

    return stored_note


async def publish_note_to_notion(
    note_id: str,
    buckets: list[str],
    settings: Settings,
) -> Optional[str]:
    """Deferred publish: create the Notion page for a Review-Kept note.

    Rebuilds the CategorizedContent snapshot persisted at capture time
    (notes.content_json), creates the page, records the page id back on the
    note, and syncs the full multi-bucket assignment. Returns the page id,
    or None if the note has no snapshot / Notion is unavailable (the local
    review state is authoritative either way).
    """
    from src.integrations.notion_sync import update_page_buckets
    from src.pipeline.database import get_note_row, set_note_notion_page

    row = await get_note_row(note_id)
    if row is None:
        logger.warning("publish_note_to_notion: note %s not found", note_id)
        return None
    if row.get("notion_page_id"):
        # Already published — just sync buckets.
        await update_page_buckets(row["notion_page_id"], buckets, settings)
        return row["notion_page_id"]

    raw = row.get("content_json")
    if not raw:
        logger.warning(
            "publish_note_to_notion: note %s has no content snapshot "
            "(captured before publish-on-keep) — skipping Notion create",
            note_id,
        )
        return None

    try:
        content = CategorizedContent.model_validate_json(raw)
    except Exception:
        logger.exception("publish_note_to_notion: bad content snapshot for %s", note_id)
        return None

    if buckets:
        content.bucket = buckets[0]

    page_id = await save_to_notion(content, settings)
    if not page_id:
        return None

    await set_note_notion_page(note_id, page_id)
    if len(buckets) > 1:
        await update_page_buckets(page_id, buckets, settings)
    logger.info("Published kept note %s to Notion page %s", note_id, page_id)
    return page_id
