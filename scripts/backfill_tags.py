"""One-off backfill: re-categorize broken notes with new tagging pipeline.

Usage:
    python -m scripts.backfill_tags --dry-run    # show what would change
    python -m scripts.backfill_tags              # actually re-process

Each note gets re-extracted (Jina/etc) and re-categorized through the new
pipeline (shopping/fashion domains, denylist, two-pass verification).
Expect 2x API calls per note due to the verifier.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure the project root is on sys.path when invoked as python -m scripts.backfill_tags
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded audit targets (note_id -> capture_id, title, old_domains)
# IDs verified by title-matching against docs/notes.json and SQLite on 2026-05-22.
# ---------------------------------------------------------------------------

AUDIT_TARGETS: list[dict] = [
    {
        "note_id": "5e73e0af02b9",
        "capture_id": "51748d10e15b",
        "title": "The Souled Store Women T-Shirts",
        "old_domains": [
            "cooking", "fitness", "data-engineering", "gen-ai", "data-science",
            "computer-science", "job-search", "personal-finance", "wedding",
            "politics", "india", "ireland", "anime", "market-intelligence",
            "applied-ai", "quantum-computing",
        ],
        "expected_domains": ["shopping", "fashion"],
    },
    {
        "note_id": "cb670af149de",
        "capture_id": "988d34160d46",
        "title": "Souled Socks Expression",
        "old_domains": [
            "cooking", "fitness", "personal-finance", "wedding", "politics",
            "india", "ireland", "anime", "market-intelligence", "applied-ai",
            "quantum-computing",
        ],
        "expected_domains": ["shopping", "fashion"],
    },
    {
        "note_id": "91ddf445f677",
        "capture_id": "27069db46226",
        "title": "Korean Pants Charcoal Grey",
        "old_domains": ["cooking", "fitness", "personal-finance"],
        "expected_domains": ["shopping", "fashion"],
    },
    {
        "note_id": "5a752ae624f2",
        "capture_id": "a483b283341e",
        "title": "NiTHO PS5 Controller Cover Case",
        "old_domains": ["computer-science"],
        "expected_domains": ["shopping"],
    },
    {
        "note_id": "4fc039987280",
        "capture_id": "4ae313d0974e",
        "title": "Spigen Liquid Cover",
        "old_domains": ["tech"],
        "expected_domains": ["shopping"],
    },
    {
        "note_id": "53f327b2dd16",
        "capture_id": "b2991e8cd761",
        "title": "Sony WH-1000XM6 Headphones",
        "old_domains": ["tech"],
        "expected_domains": ["shopping"],
    },
    {
        "note_id": "f1e08143a36f",
        "capture_id": "b34109d4ec93",
        "title": "Top 5 Wired Earphones 2026",
        "old_domains": ["tech"],
        "expected_domains": ["computer-science"],
    },
    {
        "note_id": "8c2f9b1acfb6",
        "capture_id": "fe5ea280c6b3",
        "title": "Jordan Session Men's Shoes",
        "old_domains": ["politics", "ireland", "market-intelligence"],
        "expected_domains": ["shopping", "fashion"],
    },
    {
        "note_id": "9f7f94d5b79d",
        "capture_id": "a532010f4f3d",
        "title": "Wayfair.co.uk",
        "old_domains": ["cooking", "gen-ai", "job-search"],
        "expected_domains": ["shopping"],
    },
    {
        "note_id": "63cba7ea3e13",
        "capture_id": "338ddc41d1a4",
        "title": "How to Train Your Mind (Musashi)",
        "old_domains": ["fitness", "cooking"],
        "expected_domains": ["personal-development"],
    },
    {
        "note_id": "4ac43dc7965d",
        "capture_id": "eae3e18dfb9c",
        "title": "THE SILENT PREDATOR (dark psychology)",
        "old_domains": ["fitness"],
        "expected_domains": ["personal-development"],
    },
    {
        "note_id": "f8e840041092",
        "capture_id": "8e8b676294d5",
        "title": "Wix Website Builder",
        "old_domains": ["data-engineering", "gen-ai", "computer-science"],
        "expected_domains": ["market-intelligence"],
    },
]


# ---------------------------------------------------------------------------
# Live DB lookup helpers (verify IDs still exist before reprocessing)
# ---------------------------------------------------------------------------


async def _check_capture_exists(capture_id: str) -> bool:
    """Return True if the capture row exists in SQLite."""
    from src.pipeline.database import get_capture_by_id
    row = await get_capture_by_id(capture_id)
    return row is not None


async def _get_current_domains(note_id: str) -> list[str] | None:
    """Return the current domains list for a note, or None if not in DB."""
    from src.pipeline.database import _get_db  # noqa: PLC2701 — internal helper
    db = _get_db()
    cursor = await db.execute(
        "SELECT domains FROM notes WHERE id = ?", (note_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return json.loads(row["domains"])


# ---------------------------------------------------------------------------
# Core backfill logic
# ---------------------------------------------------------------------------


async def backfill(*, dry_run: bool = False) -> None:
    """Re-process each audit target through the fixed pipeline."""
    from src.config.settings import get_settings
    from src.pipeline.database import init_db
    from src.pipeline.processor import reprocess_capture

    settings = get_settings()
    await init_db(settings.db_path)

    logger.info(
        "Starting backfill — %d notes | dry_run=%s",
        len(AUDIT_TARGETS),
        dry_run,
    )

    succeeded = 0
    skipped = 0
    failed = 0

    for target in AUDIT_TARGETS:
        note_id = target["note_id"]
        capture_id = target["capture_id"]
        title = target["title"]
        old_domains = target["old_domains"]
        expected = target["expected_domains"]

        # Verify the capture still exists in SQLite
        if not await _check_capture_exists(capture_id):
            logger.warning(
                "SKIP  %s | capture %s not found in DB (already deleted?)",
                title,
                capture_id,
            )
            skipped += 1
            continue

        # Check current domains in DB
        current_domains = await _get_current_domains(note_id)
        if current_domains is None:
            logger.warning(
                "SKIP  %s | note %s not found in DB (may have been re-processed already)",
                title,
                note_id,
            )
            skipped += 1
            continue

        logger.info(
            "%-50s | old=%s | expected=%s",
            title[:50],
            old_domains,
            expected,
        )

        if dry_run:
            logger.info("  [DRY-RUN] would call reprocess_capture(%s)", capture_id)
            continue

        # Run the reprocess
        try:
            from src.models.schemas import ProcessingStatus
            result = await reprocess_capture(capture_id)
            if result.status == ProcessingStatus.DONE:
                new_domains = result.note.domains if result.note else "?"
                logger.info(
                    "  OK    note=%s | new_domains=%s",
                    note_id,
                    new_domains,
                )
                succeeded += 1
            else:
                logger.error(
                    "  FAIL  note=%s | status=%s | error=%s",
                    note_id,
                    result.status,
                    result.error,
                )
                failed += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("  FAIL  note=%s | exception: %s", note_id, exc)
            failed += 1

    logger.info(
        "Backfill complete | succeeded=%d skipped=%d failed=%d dry_run=%s",
        succeeded,
        skipped,
        failed,
        dry_run,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-categorize 12 broken notes with the fixed tagging pipeline."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be re-processed without making any changes.",
    )
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
