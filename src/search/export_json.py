"""Export all notes to JSON for the dashboard website.

Generates docs/notes.json which the dashboard.html loads.
Called automatically after each new note is saved, and on a daily cron.

Usage:
    python -m src.search.export_json              # export from DB
    python -m src.search.export_json --vault      # export from Obsidian vault files
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_YAML_KV_RE = re.compile(r'^(\w[\w-]*):\s*"?([^"\n]*)"?\s*$', re.MULTILINE)
_YAML_LIST_RE = re.compile(r"^\s*-\s+(.+)$", re.MULTILINE)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse simple YAML frontmatter without PyYAML dependency."""
    result: dict[str, Any] = {}
    for match in _YAML_KV_RE.finditer(text):
        key, value = match.group(1), match.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            # Inline list: [a, b, c]
            items = [item.strip().strip("'\"") for item in value[1:-1].split(",")]
            result[key] = [i for i in items if i]
        else:
            result[key] = value
    return result


def export_from_vault(vault_path: Path, output_path: Path) -> int:
    """Export notes from Obsidian vault markdown files to JSON."""
    notes: list[dict] = []

    # Scan all markdown files
    for md_file in vault_path.rglob("*.md"):
        # Skip templates, meta, and hidden files
        relative = md_file.relative_to(vault_path)
        parts = relative.parts
        if any(p.startswith("_") or p.startswith(".") for p in parts):
            continue

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")

            # Parse frontmatter
            fm_match = _FRONTMATTER_RE.match(content)
            if not fm_match:
                continue  # Skip files without frontmatter

            fm = _parse_simple_yaml(fm_match.group(1))
            body = content[fm_match.end():]

            # Extract title from first heading or filename
            title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            title = title_match.group(1) if title_match else md_file.stem

            # Extract summary (first paragraph after heading)
            paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("#")]
            summary = paragraphs[0][:300] if paragraphs else ""

            # Extract key takeaways (bulleted list after "Key Takeaways" heading)
            takeaways: list[str] = []
            tk_match = re.search(r"##\s+Key Takeaways?\s*\n((?:\s*-\s+.+\n?)+)", body)
            if tk_match:
                takeaways = [m.strip() for m in _YAML_LIST_RE.findall(tk_match.group(1))]

            # Build tags from frontmatter
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]

            # Domains
            domain = fm.get("domain", "")
            domains = [domain] if domain else []

            note = {
                "id": md_file.stem[:12].replace(" ", "_"),
                "title": title,
                "summary": summary,
                "domains": domains,
                "tags": tags,
                "note_type": fm.get("type", "literature"),
                "source_url": fm.get("source-url", ""),
                "quality_score": int(fm.get("rating", fm.get("quality_score", "3")) or 3),
                "created_at": fm.get("created", ""),
                "key_takeaways": takeaways[:5],
                "content_preview": body[:500].strip(),
            }
            notes.append(note)

        except Exception as exc:
            logger.warning("Failed to parse %s: %s", md_file, exc)

    # Sort by creation date (newest first)
    notes.sort(key=lambda n: n.get("created_at", ""), reverse=True)

    # Write JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Exported %d notes to %s", len(notes), output_path)
    return len(notes)


async def export_from_db(db_path: str, output_path: Path) -> int:
    """Export notes from SQLite database to JSON."""
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT n.id, n.title, n.note_type, n.domains, n.tags, n.source_url, "
            "n.quality_score, n.created_at, n.summary, n.notion_page_id, "
            "n.key_takeaways, n.url_content_type, n.why_keep, n.open_loops, "
            "n.structured_data, n.bucket, n.buckets, n.personal_relevance, n.priority, "
            "n.action_items, n.reviewed_at, n.review_action, c.text "
            "FROM notes n LEFT JOIN captures c ON n.capture_id = c.id "
            "ORDER BY n.created_at DESC"
        )
        rows = await cursor.fetchall()

    notes = []
    for row in rows:
        domains = json.loads(row["domains"]) if row["domains"] else []
        tags = json.loads(row["tags"]) if row["tags"] else []

        def _safe_json(val):
            if not val:
                return [] if val == "" else {}
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return []

        sd = _safe_json(row["structured_data"])
        # Surface images at top level so the dashboard can render thumbnails
        # without having to crack open structured_data
        images = sd.get("images", []) if isinstance(sd, dict) else []

        notes.append({
            "id": row["id"],
            "title": row["title"],
            "summary": row["summary"] or "",
            "domains": domains,
            "tags": tags,
            "note_type": row["note_type"],
            "source_url": row["source_url"] or "",
            "quality_score": row["quality_score"] or 3,
            "created_at": row["created_at"] or "",
            "key_takeaways": _safe_json(row["key_takeaways"]),
            "content_preview": (row["text"] or "")[:500],
            "notion_page_id": row["notion_page_id"] or "",
            "url_content_type": row["url_content_type"] or "unknown",
            "why_keep": row["why_keep"] or "",
            "open_loops": _safe_json(row["open_loops"]),
            "structured_data": sd,
            "images": images,
            "bucket": row["bucket"] or "",
            # Multi-bucket array; falls back to the legacy single bucket so
            # pre-migration rows still render on the dashboard.
            "buckets": _safe_json(row["buckets"]) or ([row["bucket"]] if row["bucket"] else []),
            "personal_relevance": row["personal_relevance"] or 3,
            "priority": row["priority"] or "medium",
            "action_items": _safe_json(row["action_items"]),
            "reviewed_at": row["reviewed_at"] or "",
            "review_action": row["review_action"] or "",
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Exported %d notes from DB to %s", len(notes), output_path)
    return len(notes)


def main():
    import argparse
    import asyncio

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Export notes to JSON for dashboard")
    parser.add_argument("--vault", action="store_true", help="Export from Obsidian vault files")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    output = Path(args.output) if args.output else project_root / "docs" / "notes.json"

    if args.vault:
        from src.config.settings import get_settings
        settings = get_settings()
        count = export_from_vault(settings.vault_path, output)
    else:
        from src.config.settings import get_settings
        settings = get_settings()
        count = asyncio.run(export_from_db(settings.db_path, output))

    print(f"Exported {count} notes to {output}")


if __name__ == "__main__":
    main()
