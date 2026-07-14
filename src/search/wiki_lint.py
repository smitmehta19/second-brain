"""Read-only vault health check.

Surfaces drift across the three layers of the Mind Palace:

* **Obsidian vault** — the markdown files under ``settings.vault_path``
* **The wiki index** — ``_Meta/index.md`` listing of canonical notes
* **SQLite** — the search database that powers ``/ask`` and ``/search``

The lint pass walks all three and reports mismatches. It NEVER writes.

The output is a small dataclass-like dict so the Telegram handler can render
it however it likes (and tests can assert on it).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path inside the markdown link of an index entry, captured as group 1.
# Matches ``- [Title](some/path.md) | ...`` where Title may contain escaped
# brackets (``\[``, ``\]``) and the path may contain escaped parens (``\(``,
# ``\)``) — which wiki_meta produces for titles like ``Foo [v2] (draft)``.
_INDEX_LINE = re.compile(
    r"^\s*-\s+\[(?:\\.|[^\]\\])*\]\(((?:\\.|[^)\\])*)\)\s*\|",
    re.MULTILINE,
)


def _unescape_path(p: str) -> str:
    """Reverse wiki_meta's link-path escaping: ``\\(`` → ``(``, ``\\)`` → ``)``."""
    return p.replace("\\(", "(").replace("\\)", ")")

# Folders that aren't user-facing content and shouldn't be expected in the index.
_SKIP_DIRS = {"_Meta", "_Attachments", ".obsidian", ".trash"}


def _walk_markdown_files(vault_path: Path) -> list[str]:
    """Return relative POSIX paths of every .md file under the vault, skipping meta dirs."""
    files: list[str] = []
    if not vault_path.exists():
        return files
    for md in vault_path.rglob("*.md"):
        # Skip files inside meta/system directories.
        rel = md.relative_to(vault_path)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        files.append(rel.as_posix())
    return files


def _parse_index(vault_path: Path) -> list[str]:
    """Return the list of paths referenced in ``_Meta/index.md`` (POSIX, deduped)."""
    index_path = vault_path / "_Meta" / "index.md"
    if not index_path.exists():
        return []
    text = index_path.read_text(encoding="utf-8")
    paths = [
        _unescape_path(m.group(1)).replace("\\", "/")
        for m in _INDEX_LINE.finditer(text)
    ]
    # Preserve order but dedupe so repeated rebuilds don't inflate the count.
    seen: set[str] = set()
    unique: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


async def _db_notes_with_paths(db_path: str) -> list[dict[str, Any]]:
    """Pull notes from SQLite that should have a vault file. Returns empty on any failure."""
    try:
        import aiosqlite
    except ImportError:
        logger.debug("aiosqlite not installed — skipping DB lint")
        return []

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, title, file_path FROM notes WHERE file_path IS NOT NULL AND file_path != ''"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception:
        logger.exception("DB lint query failed — skipping")
        return []


async def lint_vault(vault_path: Path, db_path: str | None = None) -> dict[str, Any]:
    """Run a read-only health check across vault + index + DB.

    Returns a dict with three drift lists and a summary.
    """
    md_files = _walk_markdown_files(vault_path)
    md_set = set(md_files)

    indexed = _parse_index(vault_path)
    indexed_set = set(indexed)

    # Files that exist on disk but are missing from the index.
    files_missing_from_index = sorted(md_set - indexed_set)

    # Index entries whose target file no longer exists.
    index_pointing_to_missing = sorted(indexed_set - md_set)

    # DB drift — notes whose recorded file_path no longer exists on disk.
    db_drift: list[dict[str, Any]] = []
    if db_path:
        rows = await _db_notes_with_paths(db_path)
        for row in rows:
            fp = (row.get("file_path") or "").replace("\\", "/")
            if fp and fp not in md_set:
                db_drift.append({"id": row["id"], "title": row["title"], "file_path": fp})

    return {
        "vault_path": str(vault_path),
        "total_md_files": len(md_files),
        "total_index_entries": len(indexed),
        "files_missing_from_index": files_missing_from_index,
        "index_pointing_to_missing": index_pointing_to_missing,
        "db_drift": db_drift,
        "is_clean": (
            not files_missing_from_index
            and not index_pointing_to_missing
            and not db_drift
        ),
    }


def format_lint_report(report: dict[str, Any], max_examples: int = 5) -> str:
    """Render a lint report as compact markdown for Telegram."""
    lines = ["🩺 *Vault lint*"]
    lines.append(
        f"`{report['total_md_files']}` files · `{report['total_index_entries']}` index entries"
    )

    if report["is_clean"]:
        lines.append("\n✅ *Clean.* Vault, index, and DB are in sync.")
        return "\n".join(lines)

    miss_idx = report["files_missing_from_index"]
    if miss_idx:
        lines.append(f"\n📂 *Files not in index:* {len(miss_idx)}")
        for p in miss_idx[:max_examples]:
            lines.append(f"  • `{p}`")
        if len(miss_idx) > max_examples:
            lines.append(f"  _(+{len(miss_idx) - max_examples} more)_")

    dead_idx = report["index_pointing_to_missing"]
    if dead_idx:
        lines.append(f"\n💀 *Index entries with missing files:* {len(dead_idx)}")
        for p in dead_idx[:max_examples]:
            lines.append(f"  • `{p}`")
        if len(dead_idx) > max_examples:
            lines.append(f"  _(+{len(dead_idx) - max_examples} more)_")

    db_drift = report["db_drift"]
    if db_drift:
        lines.append(f"\n🗄️ *DB notes with missing files:* {len(db_drift)}")
        for row in db_drift[:max_examples]:
            lines.append(f"  • `{row['id']}` — {row['title']}")
        if len(db_drift) > max_examples:
            lines.append(f"  _(+{len(db_drift) - max_examples} more)_")

    return "\n".join(lines)
