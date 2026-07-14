"""Obsidian vault sync — writes categorized content as markdown notes."""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config.domains import DOMAINS, VAULT_STRUCTURE
from src.config.settings import Settings
from src.integrations.wiki_meta import append_log, today_utc, update_index
from src.models.schemas import (
    CategorizedContent,
    NoteType,
    ProcessingStatus,
    StoredNote,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault initialization
# ---------------------------------------------------------------------------

async def init_vault(vault_path: Path) -> None:
    """Create the full Obsidian folder structure if it doesn't exist."""
    for folder in VAULT_STRUCTURE:
        (vault_path / folder).mkdir(parents=True, exist_ok=True)
    logger.info("Vault structure initialized at %s", vault_path)


# ---------------------------------------------------------------------------
# File-name helpers
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize(name: str) -> str:
    """Remove characters that are illegal in file names."""
    return _UNSAFE_CHARS.sub("", name).strip().rstrip(".")


def _generate_filename(content: CategorizedContent) -> str:
    """Build a file name (without extension) based on note type conventions."""
    title = content.extracted.title or "Untitled"
    author = content.extracted.author
    today = datetime.utcnow().strftime("%Y-%m-%d")

    if content.note_type == NoteType.LITERATURE:
        if author:
            name = f"{author} - {title}"
        else:
            name = title

    elif content.note_type == NoteType.FLEETING:
        name = f"{today} - {title}"

    elif content.note_type == NoteType.RECIPE:
        name = f"Recipe - {title}"

    elif content.note_type == NoteType.EVERGREEN:
        # Evergreen notes should be declarative statements; use the title as-is
        name = title

    else:
        name = title

    return _sanitize(name)


def _unique_path(base: Path) -> Path:
    """If *base* already exists, append a numeric suffix."""
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    parent = base.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Frontmatter & body generation
# ---------------------------------------------------------------------------

def _moc_links(domains: list[str]) -> list[str]:
    """Return ``[[MOC - X]]`` wiki-links for every matched domain."""
    links: list[str] = []
    for domain_key in domains:
        domain_cfg = DOMAINS.get(domain_key)
        if domain_cfg:
            links.append(f"[[{domain_cfg['moc']}]]")
    return links


def _yaml_list(items: list[str]) -> str:
    """Format a list as inline YAML ``[a, b, c]``."""
    inner = ", ".join(items)
    return f"[{inner}]"


def _yaml_block_list(items: list[str]) -> str:
    """Format a list as block YAML entries."""
    return "\n".join(f"  - {item}" for item in items)


def _today() -> str:
    return today_utc()


def _build_literature_note(content: CategorizedContent) -> str:
    """Full markdown for a LITERATURE note."""
    e = content.extracted
    today = _today()
    primary_domain = content.domains[0] if content.domains else "general"

    takeaways_yaml = _yaml_block_list(content.key_takeaways) if content.key_takeaways else ""
    tags_yaml = _yaml_block_list(content.tags) if content.tags else ""

    frontmatter_lines = [
        "---",
        "type: literature",
        f'created: "{today}"',
        "status: processed",
        f"source-type: {e.content_type.value}",
    ]
    if e.url:
        frontmatter_lines.append(f'source-url: "{e.url}"')
    if e.author:
        frontmatter_lines.append(f'author: "{e.author}"')
    frontmatter_lines.append(f"domain: {primary_domain}")
    frontmatter_lines.append(f"rating: {content.quality_score}")
    if content.key_takeaways:
        frontmatter_lines.append("key-takeaways:")
        frontmatter_lines.append(takeaways_yaml)
    if content.tags:
        frontmatter_lines.append("tags:")
        frontmatter_lines.append(tags_yaml)
    frontmatter_lines.append("---")

    moc_links = _moc_links(content.domains)
    connections_section = "\n".join(f"- {link}" for link in moc_links)

    takeaways_body = "\n".join(f"- {t}" for t in content.key_takeaways) if content.key_takeaways else ""

    body = "\n".join([
        f"# {e.title}",
        "",
        "## Summary",
        e.summary or "No summary available.",
        "",
        "## Key Takeaways",
        takeaways_body or "- (none)",
        "",
        "## Notes",
        e.content or "",
        "",
        "## Connections",
        connections_section or "- (none)",
        "",
        "---",
        f"*Captured via Second Brain on {today}*",
    ])

    return "\n".join(frontmatter_lines) + "\n\n" + body + "\n"


def _build_recipe_note(content: CategorizedContent) -> str:
    """Full markdown for a RECIPE note."""
    e = content.extracted
    today = _today()

    # Try to detect cuisine / diet from tags
    cuisine = "unknown"
    diet = "unknown"
    for tag in content.tags:
        if tag.startswith("cuisine/"):
            cuisine = tag.split("/", 1)[1]
        if tag.startswith("diet/"):
            diet = tag.split("/", 1)[1]

    tags_inline = _yaml_list(content.tags) if content.tags else "[]"

    frontmatter = "\n".join([
        "---",
        "type: recipe",
        f'created: "{today}"',
        f'source-url: "{e.url or ""}"',
        f"cuisine: {cuisine}",
        f"diet: {diet}",
        f"tags: {tags_inline}",
        "---",
    ])

    body = "\n".join([
        f"# Recipe - {e.title}",
        "",
        "## Ingredients",
        "(extracted from content)",
        "",
        "## Instructions",
        e.content or "(extracted from content)",
        "",
        "## Source",
        f"[Original]({e.url})" if e.url else "(no source URL)",
    ])

    return frontmatter + "\n" + body + "\n"


def _build_fleeting_note(content: CategorizedContent) -> str:
    """Full markdown for a FLEETING note."""
    e = content.extracted
    today = _today()
    primary_domain = content.domains[0] if content.domains else "general"
    tags_inline = _yaml_list(content.tags) if content.tags else "[]"

    frontmatter = "\n".join([
        "---",
        "type: fleeting",
        f'created: "{today}"',
        "status: inbox",
        f"domain: {primary_domain}",
        f"tags: {tags_inline}",
        "---",
    ])

    body = "\n".join([
        "",
        f"# {e.title}",
        "",
        e.content or "",
        "",
        "---",
        "*Process this: convert to evergreen, link to MOC, or delete.*",
    ])

    return frontmatter + body + "\n"


def _build_evergreen_note(content: CategorizedContent) -> str:
    """Full markdown for an EVERGREEN note."""
    e = content.extracted
    today = _today()
    primary_domain = content.domains[0] if content.domains else "general"
    tags_yaml = _yaml_block_list(content.tags) if content.tags else ""

    frontmatter_lines = [
        "---",
        "type: evergreen",
        f'created: "{today}"',
        "status: processed",
        f"domain: {primary_domain}",
    ]
    if content.tags:
        frontmatter_lines.append("tags:")
        frontmatter_lines.append(tags_yaml)
    frontmatter_lines.append("---")

    moc_links = _moc_links(content.domains)
    connections = "\n".join(f"- {link}" for link in moc_links) if moc_links else "- (none)"

    body = "\n".join([
        "",
        f"# {e.title}",
        "",
        e.summary or "",
        "",
        e.content or "",
        "",
        "## Connections",
        connections,
        "",
        "---",
        f"*Captured via Second Brain on {today}*",
    ])

    return "\n".join(frontmatter_lines) + body + "\n"


def _build_generic_note(content: CategorizedContent) -> str:
    """Fallback for PROJECT, REFERENCE, PERSON, etc."""
    e = content.extracted
    today = _today()
    tags_inline = _yaml_list(content.tags) if content.tags else "[]"

    frontmatter = "\n".join([
        "---",
        f"type: {content.note_type.value}",
        f'created: "{today}"',
        "status: processed",
        f"tags: {tags_inline}",
        "---",
    ])

    moc_links = _moc_links(content.domains)
    connections = "\n".join(f"- {link}" for link in moc_links) if moc_links else ""

    body = "\n".join([
        "",
        f"# {e.title}",
        "",
        e.summary or "",
        "",
        e.content or "",
    ])

    if connections:
        body += "\n\n## Connections\n" + connections

    body += f"\n\n---\n*Captured via Second Brain on {today}*\n"

    return frontmatter + body


_BUILDERS = {
    NoteType.LITERATURE: _build_literature_note,
    NoteType.FLEETING: _build_fleeting_note,
    NoteType.RECIPE: _build_recipe_note,
    NoteType.EVERGREEN: _build_evergreen_note,
}


# ---------------------------------------------------------------------------
# Wiki maintenance — index.md + log.md
# Cross-process safe implementations live in src.integrations.wiki_meta;
# this module just re-imports them above so save_to_obsidian can call them.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Attachment handling
# ---------------------------------------------------------------------------

def _copy_attachments(
    images: list[str],
    vault_path: Path,
) -> list[str]:
    """Copy image files into ``_Attachments/`` and return embed strings."""
    attachments_dir = vault_path / "_Attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    embeds: list[str] = []
    for img_path_str in images:
        src = Path(img_path_str)
        if not src.is_file():
            logger.warning("Image not found, skipping: %s", src)
            continue
        dest = _unique_path(attachments_dir / src.name)
        shutil.copy2(src, dest)
        embeds.append(f"![[{dest.name}]]")
        logger.debug("Copied attachment %s -> %s", src, dest)
    return embeds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def save_to_obsidian(
    content: CategorizedContent,
    settings: Settings,
) -> StoredNote:
    """Write a categorized note to the Obsidian vault and return metadata."""
    vault = settings.vault_path
    await init_vault(vault)

    # Determine target folder
    folder = content.folder or "00_Inbox"
    target_dir = vault / folder
    target_dir.mkdir(parents=True, exist_ok=True)

    # Build the markdown body
    builder = _BUILDERS.get(content.note_type, _build_generic_note)
    markdown = builder(content)

    # Handle attachments
    if content.extracted.images:
        embeds = _copy_attachments(content.extracted.images, vault)
        if embeds:
            attachment_section = "\n\n## Attachments\n" + "\n".join(embeds) + "\n"
            # Insert before the trailing captured-via line
            markdown = markdown.rstrip("\n") + attachment_section

    # Write the file
    filename = _generate_filename(content)
    file_path = _unique_path(target_dir / f"{filename}.md")
    file_path.write_text(markdown, encoding="utf-8")

    relative_path = file_path.relative_to(vault)
    logger.info("Saved Obsidian note: %s", relative_path)

    today = _today()
    try:
        update_index(
            vault, content.extracted.title, relative_path.as_posix(),
            content.domains, today,
        )
        append_log(vault, content.extracted.title, today)
    except Exception:
        logger.exception("Failed to update vault index/log — note still saved")

    note_id = uuid.uuid4().hex[:12]

    return StoredNote(
        id=note_id,
        title=content.extracted.title,
        file_path=str(relative_path),
        note_type=content.note_type,
        domains=content.domains,
        tags=content.tags,
        source_url=content.extracted.url,
        summary=content.extracted.summary,
        key_takeaways=content.key_takeaways,
        quality_score=content.quality_score,
        status=ProcessingStatus.DONE,
    )
