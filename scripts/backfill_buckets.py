"""One-off script to assign bucket to existing notes using the default mapping.

No AI calls — purely deterministic based on url_content_type, note_type, source_url.
Idempotent: running twice is safe (only updates rows where bucket IS NULL).

Usage:
    python scripts/backfill_buckets.py            # write to DB + re-export notes.json
    python scripts/backfill_buckets.py --dry-run  # show what would change, no writes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure the project root is on the path so src.* imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.config.buckets import default_bucket  # noqa: E402
from src.config.settings import get_settings  # noqa: E402


async def run(dry_run: bool) -> None:
    import aiosqlite

    settings = get_settings()
    db_path = settings.db_path

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Fetch all notes (bucket column may not exist yet — handled by migration)
        cursor = await db.execute(
            "SELECT id, url_content_type, note_type, source_url, bucket FROM notes"
        )
        rows = await cursor.fetchall()

    updates: list[tuple[str, str, str]] = []  # (note_id, old_bucket, new_bucket)

    for row in rows:
        note_id = row["id"]
        existing_bucket = row["bucket"]
        if existing_bucket:
            # Already has a bucket — skip (idempotent)
            continue

        new_bucket = default_bucket(
            row["url_content_type"],
            row["note_type"],
            row["source_url"],
        )
        updates.append((note_id, existing_bucket or "", new_bucket))

    print(f"Notes without bucket: {len(updates)} / {len(rows)} total")

    if not updates:
        print("Nothing to backfill.")
        return

    for note_id, old, new in updates:
        print(f"  {note_id}: {old or '(none)'!r} -> {new!r}")

    if dry_run:
        print("\n[dry-run] No changes written.")
        return

    # Write to SQLite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        for note_id, _, new_bucket in updates:
            await db.execute(
                "UPDATE notes SET bucket = ? WHERE id = ? AND (bucket IS NULL OR bucket = '')",
                (new_bucket, note_id),
            )
        await db.commit()

    print(f"\nUpdated {len(updates)} notes in SQLite.")

    # Re-export notes.json
    from src.search.export_json import export_from_db

    output_path = _PROJECT_ROOT / "docs" / "notes.json"
    count = await export_from_db(db_path, output_path)
    print(f"Re-exported {count} notes to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill bucket column for existing notes")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to DB",
    )
    args = parser.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
