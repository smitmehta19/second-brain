"""Idempotent one-shot migration: legacy `bucket` -> `buckets` array.

For every note in docs/notes.json:
  * If `buckets` already present as a non-empty list, leave it alone.
  * Else if legacy `bucket` is present, set `buckets = [bucket]`.
  * Else default to ["DUMP"] (the catchall).

Writes atomically: notes.json.tmp then rename.  Safe to re-run.

Usage:
    python -m scripts.migrate_buckets
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    notes_file = repo_root / "docs" / "notes.json"

    if not notes_file.exists():
        print(f"notes.json not found at {notes_file}", file=sys.stderr)
        return 1

    try:
        notes = json.loads(notes_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Failed to parse notes.json: {exc}", file=sys.stderr)
        return 1

    if not isinstance(notes, list):
        print("notes.json must be a JSON array", file=sys.stderr)
        return 1

    migrated = 0
    untouched = 0
    defaulted = 0

    for n in notes:
        if not isinstance(n, dict):
            continue
        existing = n.get("buckets")
        if isinstance(existing, list) and existing:
            untouched += 1
            continue
        legacy = n.get("bucket")
        if isinstance(legacy, str) and legacy.strip():
            n["buckets"] = [legacy.strip().upper()]
            migrated += 1
        else:
            n["buckets"] = ["DUMP"]
            defaulted += 1

    if migrated == 0 and defaulted == 0:
        print(
            f"No changes needed ({untouched} notes already have buckets[])."
        )
        return 0

    tmp = notes_file.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(notes_file)

    print(
        f"Migrated {migrated}, defaulted {defaulted}, untouched {untouched}. "
        f"Total notes: {len(notes)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
