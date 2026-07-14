"""Notion → Obsidian sync script.

Pulls pages from Notion databases and writes them as Obsidian markdown files.
Run locally on a schedule (Windows Task Scheduler or manual).

Usage:
    python -m src.sync.notion_to_obsidian              # sync all new/updated
    python -m src.sync.notion_to_obsidian --full        # full re-sync
    python -m src.sync.notion_to_obsidian --since 2h    # sync last 2 hours
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from notion_client import Client as NotionClient

from src.integrations.wiki_meta import append_log, today_utc, update_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Where we store the last sync timestamp
_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "sync_state.json"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _last_sync_time(state: dict) -> Optional[datetime]:
    ts = state.get("last_sync")
    if ts:
        return datetime.fromisoformat(ts)
    return None


# ---------------------------------------------------------------------------
# Notion page fetching
# ---------------------------------------------------------------------------

def _query_database(
    notion: NotionClient,
    database_id: str,
    since: Optional[datetime] = None,
) -> list[dict]:
    """Fetch all pages from a Notion database, optionally filtered by last_edited_time."""
    filter_payload: dict[str, Any] = {}
    if since:
        filter_payload = {
            "filter": {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "after": since.isoformat(),
                },
            }
        }

    # notion-client 3.x: databases.query was removed; query data sources instead.
    db_meta = notion.databases.retrieve(database_id=database_id)
    data_source_ids = [ds["id"] for ds in db_meta.get("data_sources", [])]
    if not data_source_ids:
        logger.warning("Database %s has no data sources", database_id[:8])
        return []

    pages: list[dict] = []
    for ds_id in data_source_ids:
        has_more = True
        start_cursor: Optional[str] = None
        while has_more:
            kwargs: dict[str, Any] = {"data_source_id": ds_id, **filter_payload}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor

            response = notion.data_sources.query(**kwargs)
            pages.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

    logger.info("Fetched %d pages from database %s", len(pages), database_id[:8])
    return pages


def _get_page_blocks(notion: NotionClient, page_id: str) -> list[dict]:
    """Fetch all blocks (content) of a Notion page."""
    blocks: list[dict] = []
    has_more = True
    start_cursor: Optional[str] = None

    while has_more:
        kwargs: dict[str, Any] = {"block_id": page_id}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor

        response = notion.blocks.children.list(**kwargs)
        blocks.extend(response.get("results", []))
        has_more = response.get("has_more", False)
        start_cursor = response.get("next_cursor")

    return blocks


# ---------------------------------------------------------------------------
# Notion → Markdown conversion
# ---------------------------------------------------------------------------

def _rich_text_to_md(rich_texts: list[dict]) -> str:
    """Convert Notion rich_text array to markdown string."""
    parts = []
    for rt in rich_texts:
        text = rt.get("plain_text", "")
        annotations = rt.get("annotations", {})

        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        if annotations.get("code"):
            text = f"`{text}`"

        href = rt.get("href")
        if href:
            text = f"[{text}]({href})"

        parts.append(text)
    return "".join(parts)


def _block_to_md(block: dict) -> str:
    """Convert a single Notion block to markdown."""
    btype = block.get("type", "")
    data = block.get(btype, {})

    if btype == "paragraph":
        return _rich_text_to_md(data.get("rich_text", []))

    if btype in ("heading_1", "heading_2", "heading_3"):
        level = int(btype[-1])
        prefix = "#" * level
        return f"{prefix} {_rich_text_to_md(data.get('rich_text', []))}"

    if btype == "bulleted_list_item":
        return f"- {_rich_text_to_md(data.get('rich_text', []))}"

    if btype == "numbered_list_item":
        return f"1. {_rich_text_to_md(data.get('rich_text', []))}"

    if btype == "to_do":
        checked = "x" if data.get("checked") else " "
        return f"- [{checked}] {_rich_text_to_md(data.get('rich_text', []))}"

    if btype == "toggle":
        return f"> {_rich_text_to_md(data.get('rich_text', []))}"

    if btype == "code":
        lang = data.get("language", "")
        code = _rich_text_to_md(data.get("rich_text", []))
        return f"```{lang}\n{code}\n```"

    if btype == "quote":
        return f"> {_rich_text_to_md(data.get('rich_text', []))}"

    if btype == "callout":
        emoji = data.get("icon", {}).get("emoji", "")
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"> {emoji} {text}"

    if btype == "divider":
        return "---"

    if btype == "bookmark":
        url = data.get("url", "")
        return f"[Bookmark]({url})"

    if btype == "image":
        img = data.get("file", data.get("external", {}))
        url = img.get("url", "")
        caption = _rich_text_to_md(data.get("caption", []))
        return f"![{caption}]({url})"

    # Fallback
    return ""


def _blocks_to_markdown(blocks: list[dict]) -> str:
    """Convert all blocks to a single markdown string."""
    lines = []
    for block in blocks:
        md = _block_to_md(block)
        if md:
            lines.append(md)
        else:
            lines.append("")  # empty block = blank line
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Property extraction
# ---------------------------------------------------------------------------

def _get_property(props: dict, name: str, default: Any = None) -> Any:
    """Extract a property value from Notion page properties."""
    prop = props.get(name)
    if not prop:
        return default

    ptype = prop.get("type", "")

    if ptype == "title":
        return _rich_text_to_md(prop.get("title", []))
    if ptype == "rich_text":
        return _rich_text_to_md(prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", default) if sel else default
    if ptype == "multi_select":
        return [s.get("name", "") for s in prop.get("multi_select", [])]
    if ptype == "url":
        return prop.get("url", default)
    if ptype == "number":
        return prop.get("number", default)
    if ptype == "date":
        date_obj = prop.get("date")
        return date_obj.get("start", default) if date_obj else default
    if ptype == "checkbox":
        return prop.get("checkbox", default)

    return default


# ---------------------------------------------------------------------------
# Markdown file generation
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize(name: str) -> str:
    return _UNSAFE_CHARS.sub("", name).strip().rstrip(".")[:200]


def _page_to_obsidian(
    notion: NotionClient,
    page: dict,
    vault_path: Path,
) -> Optional[Path]:
    """Convert a Notion page to an Obsidian markdown note and write it."""
    props = page.get("properties", {})

    # Extract properties
    title = _get_property(props, "Title") or _get_property(props, "Name") or "Untitled"
    domain = _get_property(props, "Domain", "general")
    note_type = _get_property(props, "Note Type", "literature")
    tags = _get_property(props, "Tags", [])
    source_url = _get_property(props, "Source URL", "")
    status = _get_property(props, "Status", "processed")
    quality = _get_property(props, "Quality", 3)
    created = _get_property(props, "Created") or page.get("created_time", "")[:10]

    # Fetch page content
    blocks = _get_page_blocks(notion, page["id"])
    content = _blocks_to_markdown(blocks)

    # Determine target folder
    from src.config.domains import DOMAINS
    domain_cfg = DOMAINS.get(domain, {})
    folder = domain_cfg.get("obsidian_folder", "00_Inbox")
    moc = domain_cfg.get("moc", "")

    # Build frontmatter
    tags_yaml = ", ".join(tags) if isinstance(tags, list) else tags
    frontmatter = "\n".join([
        "---",
        f"type: {note_type}",
        f'created: "{created}"',
        f"status: {status}",
        f"domain: {domain}",
        f"rating: {quality}",
        f'source-url: "{source_url}"' if source_url else 'source-url: ""',
        f"tags: [{tags_yaml}]",
        f'notion-id: "{page["id"]}"',
        "---",
    ])

    # Build body
    body_parts = [f"# {title}", ""]
    if content:
        body_parts.append(content)
    if moc:
        body_parts.extend(["", "## Connections", f"- [[{moc}]]"])
    body_parts.extend(["", "---", f"*Synced from Notion on {datetime.now().strftime('%Y-%m-%d %H:%M')}*"])

    markdown = frontmatter + "\n\n" + "\n".join(body_parts) + "\n"

    # Write file
    target_dir = vault_path / folder
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = _sanitize(title)
    file_path = target_dir / f"{filename}.md"

    # Check if file exists and has same notion-id (update) or is new
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8")
        if f'notion-id: "{page["id"]}"' in existing:
            # Same page — overwrite (update)
            logger.info("Updating: %s", file_path.relative_to(vault_path))
        else:
            # Different page with same title — append suffix
            counter = 1
            while file_path.exists():
                file_path = target_dir / f"{filename} ({counter}).md"
                counter += 1

    file_path.write_text(markdown, encoding="utf-8")
    relative_path = file_path.relative_to(vault_path).as_posix()
    logger.info("Wrote: %s", relative_path)

    # Update vault meta-files so /ask, /lint, and the LLM-wiki layer see this note.
    try:
        domains_for_index = [domain] if domain else []
        update_index(vault_path, title, relative_path, domains_for_index, today_utc())
        append_log(vault_path, title, today_utc(), kind="sync")
    except Exception:
        logger.exception("Failed to update vault meta files for %s", relative_path)

    return file_path


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def sync(
    notion_api_key: str,
    database_ids: list[str],
    vault_path: Path,
    since: Optional[datetime] = None,
    full: bool = False,
) -> int:
    """Run the Notion → Obsidian sync. Returns count of synced pages."""
    notion = NotionClient(auth=notion_api_key)

    state = _load_state()
    if not full and since is None:
        since = _last_sync_time(state)

    total = 0
    for db_id in database_ids:
        if not db_id:
            continue
        pages = _query_database(notion, db_id, since=since)
        for page in pages:
            try:
                _page_to_obsidian(notion, page, vault_path)
                total += 1
            except Exception:
                logger.exception("Failed to sync page %s", page.get("id", "?"))

    # Update state
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    state["last_count"] = total
    _save_state(state)

    logger.info("Sync complete: %d pages synced", total)
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_since(value: str) -> datetime:
    """Parse relative time like '2h', '30m', '1d' into an absolute datetime."""
    unit = value[-1].lower()
    amount = int(value[:-1])
    delta_map = {"m": "minutes", "h": "hours", "d": "days"}
    if unit not in delta_map:
        raise ValueError(f"Unknown time unit: {unit}. Use m/h/d.")
    delta = timedelta(**{delta_map[unit]: amount})
    return datetime.now(timezone.utc) - delta


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Notion → Obsidian vault")
    parser.add_argument("--full", action="store_true", help="Full re-sync (ignore last sync time)")
    parser.add_argument("--since", type=str, help="Sync pages edited in last N (e.g. 2h, 30m, 1d)")
    parser.add_argument("--vault", type=str, default=None, help="Obsidian vault path")
    args = parser.parse_args()

    # Load settings
    from src.config.settings import get_settings
    settings = get_settings()

    vault_path = Path(args.vault) if args.vault else settings.vault_path
    if not settings.notion_api_key:
        logger.error("NOTION_API_KEY not set in .env")
        sys.exit(1)

    database_ids = [
        settings.notion_inbox_database_id,
        settings.notion_resources_database_id,
    ]
    database_ids = [d for d in database_ids if d]
    if not database_ids:
        logger.error("No Notion database IDs configured in .env")
        sys.exit(1)

    since = _parse_since(args.since) if args.since else None

    count = sync(
        notion_api_key=settings.notion_api_key,
        database_ids=database_ids,
        vault_path=vault_path,
        since=since,
        full=args.full,
    )

    print(f"\nSynced {count} pages to {vault_path}")


if __name__ == "__main__":
    main()
