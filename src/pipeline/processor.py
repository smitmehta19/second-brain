"""Main processing pipeline — orchestrates extraction, categorization, and storage.

The pipeline is the single entry point that the Telegram bot (and any other
consumer) calls to turn a :class:`RawCapture` into a persisted
:class:`StoredNote`.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.categorizer.ai_categorizer import invalidate as invalidate_categorization_cache
from src.config.settings import Settings, get_settings
from src.models.schemas import (
    CategorizedContent,
    ExtractedContent,
    NoteType,
    PipelineResult,
    ProcessingStatus,
    RawCapture,
    StoredNote,
)
from src.pipeline.database import (
    delete_note_keep_capture,
    get_capture_by_id,
    get_note_by_capture_id,
    get_stats as db_stats,
    init_db,
    save_capture,
    save_note,
    search_notes as db_search,
    update_capture_status,
    url_already_exists,
)

logger = logging.getLogger(__name__)

# Concurrency guard — initialised lazily in ``initialize_pipeline``.
_semaphore: asyncio.Semaphore | None = None


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


async def initialize_pipeline() -> None:
    """Prepare the pipeline (database + concurrency primitives).

    Must be called once during application startup.
    """
    global _semaphore  # noqa: PLW0603
    settings = get_settings()
    await init_db(settings.db_path)
    _semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    logger.info(
        "Pipeline initialised (max_concurrent_jobs=%d)", settings.max_concurrent_jobs
    )


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


async def process_capture(
    capture: RawCapture,
    *,
    reprocess_ctx: dict | None = None,
) -> PipelineResult:
    """Run the full processing pipeline for a single capture.

    Steps:
        1. Persist capture with status QUEUED
        2. Extract content
        3. Categorise with AI
        4. Store to Obsidian / Notion
        5. Persist the resulting note

    The function **never** raises — all errors are caught and returned inside
    the :class:`PipelineResult`.
    """
    sem = _semaphore or asyncio.Semaphore(5)
    start = time.monotonic()

    async with sem:
        try:
            return await _run_pipeline(capture, start, reprocess_ctx=reprocess_ctx)
        except Exception as exc:  # noqa: BLE001 — pipeline must not crash
            elapsed = int((time.monotonic() - start) * 1000)
            logger.exception("Unhandled error processing capture %s", capture.id)
            try:
                await update_capture_status(
                    capture.id, ProcessingStatus.FAILED, str(exc)
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to update status for capture %s", capture.id)
            return PipelineResult(
                raw_id=capture.id,
                status=ProcessingStatus.FAILED,
                error=str(exc),
                processing_time_ms=elapsed,
            )


async def _run_pipeline(
    capture: RawCapture,
    start: float,
    *,
    reprocess_ctx: dict | None = None,
) -> PipelineResult:
    """Internal pipeline logic — separated so the outer wrapper can catch."""

    settings = get_settings()
    is_reprocess = reprocess_ctx is not None

    # 0. URL dedup — skip on reprocess (we already deleted the old note) ----
    if capture.url and not is_reprocess:
        from src.extractors.url_detector import clean_url
        cleaned_url = clean_url(capture.url)
        capture = capture.model_copy(update={"url": cleaned_url})
        existing = await url_already_exists(cleaned_url)
        if existing:
            import json
            logger.info("Duplicate URL skipped: %s (existing note: %s)", cleaned_url, existing["id"])
            domains = json.loads(existing["domains"]) if existing.get("domains") else []
            elapsed = int((time.monotonic() - start) * 1000)
            return PipelineResult(
                raw_id=capture.id,
                status=ProcessingStatus.DONE,
                note=StoredNote(
                    id=existing["id"],
                    title=existing.get("title", ""),
                    file_path="",
                    note_type=NoteType.LITERATURE,
                    domains=domains,
                    tags=["status/duplicate"],
                    source_url=cleaned_url,
                ),
                error="duplicate",
                processing_time_ms=elapsed,
            )

    # 1. Save raw capture (skip on reprocess — capture already exists) ------
    if not is_reprocess:
        await save_capture(capture)
    await update_capture_status(capture.id, ProcessingStatus.QUEUED)

    # 2. Extract ------------------------------------------------------------
    await update_capture_status(capture.id, ProcessingStatus.EXTRACTING)
    try:
        from src.extractors import extract_content  # lazy to avoid circular imports

        extracted: ExtractedContent = await extract_content(capture)
    except Exception as exc:  # noqa: BLE001
        logger.error("Extraction failed for %s: %s", capture.id, exc)
        await update_capture_status(
            capture.id, ProcessingStatus.FAILED, f"extraction error: {exc}"
        )
        elapsed = int((time.monotonic() - start) * 1000)
        return PipelineResult(
            raw_id=capture.id,
            status=ProcessingStatus.FAILED,
            error=f"extraction error: {exc}",
            processing_time_ms=elapsed,
        )

    # 3. Categorise ---------------------------------------------------------
    await update_capture_status(capture.id, ProcessingStatus.CATEGORIZING)
    try:
        from src.categorizer.ai_categorizer import categorize  # lazy import

        categorized: CategorizedContent = await categorize(extracted, settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Categorisation failed for %s, using fallback: %s", capture.id, exc
        )
        categorized = _fallback_categorization(extracted)

    # 4. Store --------------------------------------------------------------
    await update_capture_status(capture.id, ProcessingStatus.STORING)
    try:
        from src.integrations import store_content  # lazy import

        stored: StoredNote = await store_content(
            categorized, settings, reprocess_ctx=reprocess_ctx,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Storage failed for %s: %s", capture.id, exc)
        await update_capture_status(
            capture.id, ProcessingStatus.FAILED, f"storage error: {exc}"
        )
        elapsed = int((time.monotonic() - start) * 1000)
        return PipelineResult(
            raw_id=capture.id,
            status=ProcessingStatus.FAILED,
            error=f"storage error: {exc}",
            processing_time_ms=elapsed,
        )

    # 5. Persist note & mark done ------------------------------------------
    # Inject extracted product images into structured_data so they survive into the
    # notes.json export (no DB schema change needed — structured_data is already a JSON column).
    structured_with_images = dict(categorized.structured_data or {})
    extracted_images = getattr(categorized.extracted, "images", None) or []
    if extracted_images:
        structured_with_images["images"] = extracted_images[:5]

    # Serialize the full categorized content so publish-on-keep can build the
    # Notion page later without re-extracting.
    try:
        content_snapshot: str | None = categorized.model_dump_json()
    except Exception:
        content_snapshot = None

    # Score extraction confidence for the review dashboard (best-effort —
    # never let a scoring bug block note persistence).
    try:
        from src.extractors.confidence import score_extraction  # lazy import

        extraction_confidence: float | None = score_extraction(extracted)
    except Exception:
        logger.warning(
            "Confidence scoring failed for %s, storing null", capture.id, exc_info=True
        )
        extraction_confidence = None

    try:
        await save_note(
            stored,
            capture.id,
            url_content_type=getattr(categorized.extracted, "url_content_type", "unknown"),
            why_keep=categorized.why_keep,
            open_loops=categorized.open_loops,
            structured_data=structured_with_images,
            personal_relevance=categorized.personal_relevance,
            priority=categorized.priority,
            action_items=categorized.action_items,
            bucket=categorized.bucket,
            content_json=content_snapshot,
            extraction_confidence=extraction_confidence,
        )
    except Exception as exc:
        logger.warning(
            "save_note failed for %s (note still synced to Notion/Obsidian): %s",
            capture.id, exc,
        )
    await update_capture_status(capture.id, ProcessingStatus.DONE)
    elapsed = int((time.monotonic() - start) * 1000)

    # 6. Auto-export JSON for dashboard (non-blocking) ---------------------
    try:
        import subprocess
        import sys
        from pathlib import Path
        project_root = Path(__file__).resolve().parent.parent.parent
        subprocess.Popen(
            [sys.executable, "-m", "src.search.export_json"],
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Non-critical

    logger.info(
        "Capture %s processed in %dms -> note %s",
        capture.id,
        elapsed,
        stored.id,
    )
    return PipelineResult(
        raw_id=capture.id,
        status=ProcessingStatus.DONE,
        note=stored,
        processing_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Fallback categorisation
# ---------------------------------------------------------------------------


def _fallback_categorization(extracted: ExtractedContent) -> CategorizedContent:
    """Produce a minimal categorisation when the AI categoriser is unavailable."""
    return CategorizedContent(
        extracted=extracted,
        note_type=NoteType.FLEETING,
        domains=["uncategorized"],
        tags=["status/needs-review"],
        folder="00-Inbox",
        quality_score=1,
    )


# ---------------------------------------------------------------------------
# Public helpers (delegating to database layer)
# ---------------------------------------------------------------------------


async def search_notes(query: str) -> list[dict]:
    """Search stored notes by keyword."""
    return await db_search(query)


async def get_stats() -> dict:
    """Return pipeline and note statistics, shaped for bot display."""
    raw = await db_stats()
    by_status = raw.get("by_status", {})
    # Flatten for the bot handlers
    raw["queued"] = by_status.get("queued", 0)
    raw["processing"] = (
        by_status.get("extracting", 0)
        + by_status.get("categorizing", 0)
        + by_status.get("storing", 0)
    )
    raw["failed"] = by_status.get("failed", 0)
    raw["done_today"] = by_status.get("done", 0)
    raw["top_domains"] = sorted(
        raw.get("by_domain", {}).items(), key=lambda x: x[1], reverse=True
    )
    raw["top_domains"] = [f"{k}: {v}" for k, v in raw["top_domains"][:10]]
    return raw


async def reprocess_capture(capture_id: str) -> PipelineResult:
    """Reload a capture and re-run the pipeline, updating all outputs in-place.

    Cleans up the old note from the DB (keeping the capture), then re-runs
    the full pipeline with context so Obsidian/Notion are updated rather
    than duplicated.
    """
    row = await get_capture_by_id(capture_id)
    if row is None:
        return PipelineResult(
            raw_id=capture_id,
            status=ProcessingStatus.FAILED,
            error=f"Capture {capture_id} not found",
        )

    # Build reprocess context from the old note (if any)
    reprocess_ctx: dict | None = None
    old_note = await delete_note_keep_capture(capture_id)
    if old_note:
        reprocess_ctx = {
            "old_notion_page_id": old_note.get("notion_page_id"),
        }
        logger.info(
            "Reprocessing %s — old note deleted, notion_page=%s",
            capture_id,
            reprocess_ctx.get("old_notion_page_id", "none"),
        )
    else:
        reprocess_ctx = {}

    from src.models.schemas import ContentType

    capture = RawCapture(
        id=row["id"],
        content_type=ContentType(row["content_type"]),
        text=row.get("text"),
        url=row.get("url"),
        file_path=row.get("file_path"),
        caption=row.get("caption"),
        sender=row.get("sender", "self"),
        source_chat=row.get("source_chat", "telegram"),
    )

    # Invalidate the categorization LRU cache so reprocess re-runs the AI
    # call instead of returning the stale cached CategorizedContent.
    # Key shape matches categorize(): URL for URL captures, else raw_id.
    invalidate_categorization_cache(capture.url or capture.id)

    return await process_capture(capture, reprocess_ctx=reprocess_ctx)
