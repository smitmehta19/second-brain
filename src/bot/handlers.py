"""Telegram message handlers for the Second Brain bot."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

from telegram import Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from src.config.settings import get_settings
from src.models.schemas import ContentType, PipelineResult, ProcessingStatus, RawCapture
from src.pipeline.processor import get_stats, process_capture, reprocess_capture, search_notes

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r"https?://[^\s<>\"')\]]+",
    re.IGNORECASE,
)

# Bare-domain detector — matches strings like "wix.com", "linear.app",
# "fly.io/docs" that the user typed without an http(s):// prefix. Used only
# when URL_PATTERN finds nothing, so a clean URL never produces a duplicate.
# - (?<!@) excludes email addresses (gmail.com after @)
# - (?<![a-z0-9-]) excludes substrings of larger identifiers (e.g. "fooo.com"
#   inside "subdomain.fooo.com" still matches once, but we don't double-count)
# TLD list is curated for common public TLDs the user is likely to actually
# send. Adding more is fine — risk is just false positives.
_BARE_DOMAIN_TLD = (
    r"com|org|net|io|ai|app|co|dev|so|sh|me|fyi|gg|tv|to|xyz|tech|"
    r"ie|uk|in|eu|us|de|fr|au|nz|ca|jp|cn|nl|"
    r"info|biz|news|blog|page|site|store|shop|"
    r"design|art|space|cloud|software|company"
)
BARE_DOMAIN_PATTERN = re.compile(
    r"(?<!@)(?<![a-zA-Z0-9.-])"
    r"((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    rf"(?:{_BARE_DOMAIN_TLD})"
    r"(?:/[^\s<>\"')\]},;]*)?)",
    re.IGNORECASE,
)


def _extract_urls(text: str) -> list[str]:
    """Find URLs in a message — explicit http(s) first, bare domains as fallback.

    Returns absolute URLs with a scheme prefix. Bare-domain hits get
    ``https://`` prepended. Email addresses are excluded.
    """
    urls = URL_PATTERN.findall(text)
    if urls:
        return urls
    bare = BARE_DOMAIN_PATTERN.findall(text)
    # Dedup while preserving order; add scheme.
    seen: set[str] = set()
    out: list[str] = []
    for d in bare:
        normalized = d.rstrip(".,;:!?")
        if normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        out.append(f"https://{normalized}")
    return out

WELCOME_TEXT = (
    "🏛️ *Mind Palace*\n\n"
    "Dump anything here — links, text, photos, voice, videos, "
    "forwards. AI handles the rest.\n\n"
    "*Commands:*\n"
    "/ask <question> — Ask your vault anything\n"
    "/save — File the last /ask answer as a vault note\n"
    "/search <query> — Search your brain\n"
    "/recent — Last 5 captures\n"
    "/tag <id> +tag -tag — Edit tags\n"
    "/forget <url> — Remove a URL so you can re-send it\n"
    "/delete <query> — Delete a note\n"
    "/lint — Vault health check (drift report)\n"
    "/stats — Knowledge stats\n"
    "/credits — AI usage today\n"
    "/reprocess <id> — Retry failed capture"
)


def _is_authorized(user_id: int) -> bool:
    """Check if the user is allowed to interact with the bot.

    Empty allow-list = deny everyone. Set TELEGRAM_ALLOWED_USERS in .env.
    """
    settings = get_settings()
    allowed = settings.telegram_allowed_users
    if not allowed:
        return False
    return user_id in allowed


def _reject_unauthorized(update: Update) -> bool:
    """Return True and log if user is not authorized."""
    user = update.effective_user
    if user is None or not _is_authorized(user.id):
        logger.warning("Unauthorized access attempt from user %s", user)
        return True
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — send welcome message."""
    if _reject_unauthorized(update):
        return
    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — show processing queue status."""
    if _reject_unauthorized(update):
        return
    try:
        stats = await get_stats()
        queued = stats.get("queued", 0)
        processing = stats.get("processing", 0)
        done_today = stats.get("done_today", 0)
        failed = stats.get("failed", 0)
        text = (
            "*Processing Queue*\n"
            f"Queued: {queued}\n"
            f"Processing: {processing}\n"
            f"Completed today: {done_today}\n"
            f"Failed: {failed}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Error fetching status")
        await update.message.reply_text("Failed to fetch queue status. Try again later.")


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask <question> — answer questions from the vault using AI."""
    if _reject_unauthorized(update):
        return
    question = " ".join(context.args) if context.args else ""
    if not question:
        await update.message.reply_text(
            "*Ask Your Vault*\n"
            "Usage: `/ask what was that spinach recipe?`\n"
            "Searches your saved notes and answers using AI.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    reply = await _reply_with_retry(update.message, "🧠 Searching your vault...")
    try:
        await update.message.chat.send_action(ChatAction.TYPING)

        from src.config.settings import get_settings
        from src.search.vault_qa import ask_vault

        settings = get_settings()
        answer, matched_notes = await ask_vault(question, settings)

        # Stash for /save — keep only the small fields needed to render
        # source links in the synthesis note. Storing full DB rows here
        # leaks memory across the bot's lifetime.
        slim_notes = [
            {
                "id": n.get("id"),
                "title": n.get("title", "Untitled"),
                "source_url": n.get("source_url", ""),
            }
            for n in matched_notes[:5]
        ]
        context.user_data["last_ask"] = {
            "question": question,
            "answer": answer,
            "matched_notes": slim_notes,
        }

        # Build response
        source_count = len(matched_notes)
        header = f"🏛️ *Answer from your vault* ({source_count} notes searched)\n\n"

        # Truncate for Telegram's 4096 char limit
        max_answer = 3600
        if len(answer) > max_answer:
            answer = answer[:max_answer] + "..."

        text = header + answer

        # Add source links at the bottom
        if matched_notes:
            sources = []
            for note in matched_notes[:3]:
                title = note.get("title", "Untitled")
                url = note.get("source_url", "")
                if url:
                    sources.append(f"[{_escape_md(title[:40])}]({url})")
                else:
                    sources.append(f"_{_escape_md(title[:40])}_")
            if sources:
                text += f"\n\n📚 *Sources:* {' | '.join(sources)}"

        text += "\n\n_Tap /save to file this answer into your vault._"

        try:
            await reply.edit_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        except Exception:
            # Markdown parsing can fail — retry as plain text
            await reply.edit_text(header + answer, disable_web_page_preview=True)

    except Exception:
        logger.exception("Ask command failed for question=%s", question)
        await reply.edit_text("Failed to answer. Try `/search` instead.", parse_mode=ParseMode.MARKDOWN)


async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /save — file the last /ask answer as an evergreen note in 05_Atlas/."""
    if _reject_unauthorized(update):
        return

    # Pop BEFORE doing any work so a rapid double-tap on /save can't race
    # two writers into producing two files with " (1)" suffixes.
    last = context.user_data.pop("last_ask", None)
    if not last:
        await update.message.reply_text(
            "Nothing to save yet. Run `/ask <question>` first, then `/save`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    reply = await _reply_with_retry(update.message, "💾 Saving synthesis to vault...")
    try:
        from src.config.settings import get_settings
        from src.search.vault_qa import save_synthesis_to_vault

        settings = get_settings()
        # File I/O off the event loop.
        path = await asyncio.to_thread(
            save_synthesis_to_vault,
            last["question"],
            last["answer"],
            last["matched_notes"],
            settings,
        )

        await reply.edit_text(
            f"✅ *Saved to vault*\n`{path}`\n\nOpen in Obsidian to browse it.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("save_command failed")
        # On failure, put the stash back so the user can retry without re-asking.
        context.user_data["last_ask"] = last
        await reply.edit_text("❌ Failed to save — check logs. You can /save again.")


async def lint_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /lint — read-only health check across vault, index, and DB."""
    if _reject_unauthorized(update):
        return
    reply = await _reply_with_retry(update.message, "🩺 Scanning vault...")
    try:
        from src.config.settings import get_settings
        from src.search.wiki_lint import format_lint_report, lint_vault

        settings = get_settings()
        report = await lint_vault(settings.vault_path, settings.db_path)
        text = format_lint_report(report)
        try:
            await reply.edit_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        except Exception:
            # Markdown can choke on stray underscores in filenames — fall back to plain.
            await reply.edit_text(text, disable_web_page_preview=True)
    except Exception:
        logger.exception("lint_command failed")
        await reply.edit_text("❌ Lint failed — check logs.")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /search <query> — smart ranked search across all notes."""
    if _reject_unauthorized(update):
        return
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "*Smart Search*\n"
            "Usage: `/search RAG vector database`\n"
            "Searches across titles, summaries, tags, and content.\n"
            "Results ranked by relevance.", parse_mode=ParseMode.MARKDOWN
        )
        return
    try:
        await update.message.chat.send_action(ChatAction.TYPING)

        # Try smart search first, fall back to basic DB search
        try:
            from src.search.engine import smart_search
            results = await smart_search(query, limit=8)
        except Exception:
            results_raw = await search_notes(query)
            results = [{"title": r.get("title", "Untitled"), "domains": [], "score": 1, "highlights": []} for r in results_raw[:8]]

        if not results:
            await update.message.reply_text(
                f"No results for *{query}*.\nTry broader terms or check `/stats` for your domains.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        lines = [f"🔍 *{len(results)} results for:* _{query}_\n"]
        for i, r in enumerate(results[:8], 1):
            title = r.get("title", "Untitled")
            domains = r.get("domains", [])
            domain_str = " / ".join(d.replace("-", " ").title() for d in domains[:2])
            score = r.get("score", 0)
            url = r.get("source_url", "")

            line = f"{i}. *{title}*"
            if domain_str:
                line += f"\n   📁 {domain_str}"
            highlights = r.get("highlights", [])
            if highlights:
                line += f"\n   _{highlights[0][:80]}..._"
            if url:
                line += f"\n   [Source]({url})"
            lines.append(line)

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception:
        logger.exception("Search failed for query=%s", query)
        await update.message.reply_text("Search failed. Please try again.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats — show vault statistics."""
    if _reject_unauthorized(update):
        return
    try:
        stats = await get_stats()
        total = stats.get("total_notes", 0)
        by_type = stats.get("by_type", {})
        domains = stats.get("top_domains", [])

        # Vault health
        from src.bot.daily_digest import calculate_vault_health
        health = await calculate_vault_health()
        health_emoji = "🟢" if health["score"] >= 80 else "🟡" if health["score"] >= 50 else "🔴"

        lines = [
            f"🏛️ *Mind Palace Stats*\n",
            f"📝 Total notes: *{total}*",
            f"{health_emoji} Vault health: *{health['score']}/100*",
        ]
        if health["issues"]:
            lines.append(f"⚠️ _{', '.join(health['issues'][:3])}_")
        if by_type:
            lines.append("\n*By type:*")
            type_icons = {"literature": "📄", "fleeting": "💡", "evergreen": "🌿", "recipe": "🍳", "reference": "📌"}
            for nt, count in by_type.items():
                icon = type_icons.get(nt, "📝")
                lines.append(f"  {icon} {nt}: {count}")
        if domains:
            lines.append("\n*Top domains:*")
            for domain in domains[:5]:
                lines.append(f"  {domain}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Error fetching stats")
        await update.message.reply_text("Failed to fetch stats. Try again later.")


async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /credits — show AI provider usage for today."""
    if _reject_unauthorized(update):
        return
    try:
        from src.utils.credit_tracker import get_usage
        usage = get_usage()

        lines = ["*AI Credits — Today*\n"]
        for provider, info in usage.items():
            status_emoji = {"ok": "🟢", "low": "🟡", "exhausted": "🔴"}.get(info["status"], "⚪")
            lines.append(f"{status_emoji} *{provider.title()}*")

            # Requests bar
            req_pct = round((info["requests_used"] / info["requests_limit"]) * 100, 1) if info["requests_limit"] else 0
            bar_filled = int(req_pct / 10)
            bar = "█" * bar_filled + "░" * (10 - bar_filled)
            lines.append(
                f"  Requests: `{bar}` {req_pct}%\n"
                f"  {info['requests_used']:,} / {info['requests_limit']:,}"
            )

            # Tokens (if we have live data from headers/errors)
            if info["tokens_used"] is not None:
                tok_pct = info["tokens_pct"] or 0
                tok_bar_filled = int(tok_pct / 10)
                tok_bar = "█" * tok_bar_filled + "░" * (10 - tok_bar_filled)
                lines.append(
                    f"  Tokens: `{tok_bar}` {tok_pct}%\n"
                    f"  {info['tokens_used']:,} / {info['tokens_limit']:,}"
                )
            lines.append("")

        lines.append("_Resets at midnight UTC (5:30 AM IST)_")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Credits command failed")
        await update.message.reply_text("Could not fetch credit usage.")


async def reprocess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reprocess <id> — reprocess a failed capture."""
    if _reject_unauthorized(update):
        return
    capture_id = context.args[0] if context.args else ""
    if not capture_id:
        await update.message.reply_text("Usage: /reprocess <capture_id>")
        return
    reply = await update.message.reply_text(f"Reprocessing capture `{capture_id}`...", parse_mode=ParseMode.MARKDOWN)
    try:
        result = await reprocess_capture(capture_id)
        await _edit_with_result(reply, result)
    except Exception:
        logger.exception("Reprocess failed for id=%s", capture_id)
        await reply.edit_text(f"Failed to reprocess `{capture_id}`.")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /forget <url> — remove a URL from the database so it can be re-submitted."""
    if _reject_unauthorized(update):
        return
    url = " ".join(context.args) if context.args else ""
    if not url:
        await update.message.reply_text(
            "Usage: `/forget <url>`\n"
            "Removes the URL from your brain so you can send it again.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        from src.pipeline.database import forget_url
        deleted = await forget_url(url)
        if deleted:
            await update.message.reply_text(
                f"Forgotten. Send the link again to re-process it.",
            )
        else:
            await update.message.reply_text(f"URL not found in your brain.")
    except Exception:
        logger.exception("Forget failed for url=%s", url)
        await update.message.reply_text("Failed to forget URL.")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /delete <query> — search and delete a note.

    Usage:
      /delete <id>        — delete by capture/note ID
      /delete paneer       — search, show matches, confirm
    """
    if _reject_unauthorized(update):
        return
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "*Delete a note*\n"
            "Usage:\n"
            "`/delete <note_id>` — delete by ID\n"
            "`/delete paneer recipe` — search & delete",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        from src.pipeline.database import delete_note, search_notes as db_search

        # Try as direct ID first
        deleted = await delete_note(query.strip())
        if deleted:
            await update.message.reply_text(f"🏛️ Deleted note `{query.strip()}`", parse_mode=ParseMode.MARKDOWN)
            return

        # Search for it
        results = await db_search(query)
        if not results:
            await update.message.reply_text(f"No notes found matching *{query}*", parse_mode=ParseMode.MARKDOWN)
            return

        if len(results) == 1:
            # Single match — delete it
            note = results[0]
            deleted = await delete_note(note["id"])
            if deleted:
                await update.message.reply_text(
                    f"🏛️ Deleted: *{note.get('title', 'Untitled')}*\nID: `{note['id']}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await update.message.reply_text("Failed to delete. Try with exact ID.")
        else:
            # Multiple matches — show list
            lines = [f"Found {len(results)} matches for *{query}*. Pick one:\n"]
            for note in results[:5]:
                lines.append(f"• `{note['id']}` — {note.get('title', 'Untitled')}")
            lines.append(f"\nUse `/delete <id>` to delete a specific one.")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception:
        logger.exception("Delete failed for query=%s", query)
        await update.message.reply_text("Delete failed. Try again.")


async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /recent — show last 5 captures."""
    if _reject_unauthorized(update):
        return
    try:
        from src.pipeline.database import get_recent_captures
        captures = await get_recent_captures(limit=5)
        if not captures:
            await update.message.reply_text("No captures yet. Send me something!")
            return

        lines = ["*Recent captures:*\n"]
        for c in captures:
            status_icon = "✅" if c.get("status") == "done" else "⏳" if c.get("status") == "queued" else "❌"
            title = c.get("text", c.get("url", ""))[:50] or "(media)"
            lines.append(f"{status_icon} `{c['id']}` — {title}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Recent command failed")
        await update.message.reply_text("Failed to fetch recent captures.")


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tag <id> <+tag or -tag> — add or remove tags from a note.

    Usage:
      /tag abc123 +domain/cooking       — add a tag
      /tag abc123 -domain/fitness       — remove a tag
      /tag abc123 +recipe +domain/india — add multiple tags
      /tag abc123                       — show current tags
    """
    if _reject_unauthorized(update):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "*Edit tags*\n"
            "Usage:\n"
            "`/tag <note_id> +tag` — add tag\n"
            "`/tag <note_id> -tag` — remove tag\n"
            "`/tag <note_id>` — show tags\n\n"
            "Example: `/tag abc123 +domain/cooking -domain/fitness`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    note_id = args[0]
    tag_ops = args[1:]

    try:
        from src.pipeline.database import _get_db
        import json

        db = _get_db()
        cursor = await db.execute("SELECT id, title, tags FROM notes WHERE id = ?", (note_id,))
        row = await cursor.fetchone()

        if not row:
            await update.message.reply_text(f"Note `{note_id}` not found.", parse_mode=ParseMode.MARKDOWN)
            return

        title = row["title"]
        tags = json.loads(row["tags"]) if row["tags"] else []

        if not tag_ops:
            # Show current tags
            tags_str = "\n".join(f"  • `{t}`" for t in tags) if tags else "  (none)"
            await update.message.reply_text(
                f"*{title}*\n\nTags:\n{tags_str}\n\n"
                f"Use `/tag {note_id} +new-tag` to add",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Process +add and -remove operations
        added = []
        removed = []
        for op in tag_ops:
            if op.startswith("+"):
                tag = op[1:]
                if tag and tag not in tags:
                    tags.append(tag)
                    added.append(tag)
            elif op.startswith("-"):
                tag = op[1:]
                if tag in tags:
                    tags.remove(tag)
                    removed.append(tag)

        # Save back
        await db.execute(
            "UPDATE notes SET tags = ? WHERE id = ?",
            (json.dumps(tags), note_id),
        )
        await db.commit()

        lines = [f"🏷️ *{title}*\n"]
        if added:
            lines.append("Added: " + ", ".join(f"`+{t}`" for t in added))
        if removed:
            lines.append("Removed: " + ", ".join(f"`-{t}`" for t in removed))
        lines.append(f"\nCurrent tags: {', '.join(tags[:8])}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception:
        logger.exception("Tag command failed for note=%s", note_id)
        await update.message.reply_text("Failed to update tags. Check the note ID.")


async def _reply_with_retry(message: Message, text: str, retries: int = 3) -> Message:
    """Send a reply, retrying on transient network errors."""
    for attempt in range(1, retries + 1):
        try:
            return await message.reply_text(text)
        except (NetworkError, TimedOut) as exc:
            if attempt == retries:
                raise
            await asyncio.sleep(2 * attempt)
            logger.warning("Reply failed (attempt %d/%d): %s — retrying", attempt, retries, exc)
    raise RuntimeError("unreachable")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages — detect URLs or treat as a thought."""
    if _reject_unauthorized(update):
        return
    text = update.message.text or ""
    urls = _extract_urls(text)

    reply = await _reply_with_retry(update.message, "📥 Captured! Processing...")

    result = None
    try:
        if urls:
            for url in urls:
                capture = _build_capture(update.message, content_type=ContentType.URL, url=url, text=text)
                result = await process_capture(capture)
            # Strip explicit URLs AND bare domains from the leftover thought text.
            stripped = URL_PATTERN.sub("", text)
            stripped = BARE_DOMAIN_PATTERN.sub("", stripped).strip()
            if stripped:
                text_capture = _build_capture(update.message, content_type=ContentType.TEXT, text=stripped)
                result = await process_capture(text_capture)
        else:
            capture = _build_capture(update.message, content_type=ContentType.TEXT, text=text)
            result = await process_capture(capture)

        await _edit_with_result(reply, result)
    except Exception:
        logger.exception("Failed to process text message")
        # If pipeline succeeded but _edit_with_result crashed (e.g. Markdown),
        # try a plain-text fallback so we don't show "Processing failed"
        if result and result.status == ProcessingStatus.DONE and result.note:
            try:
                await reply.edit_text(
                    f"Saved: {result.note.title}\n"
                    f"Domains: {', '.join(result.note.domains[:3])}\n"
                    f"Done in {result.processing_time_ms}ms"
                )
                return
            except Exception:
                pass
        await reply.edit_text("Processing failed. The message has been queued for retry.")


async def _handle_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    content_type: ContentType,
    label: str,
) -> None:
    """Generic handler for photo, voice, document, and video messages."""
    if _reject_unauthorized(update):
        return
    reply = await _reply_with_retry(update.message, f"Captured {label}! Processing...")
    try:
        msg = update.message
        if content_type == ContentType.IMAGE:
            media = msg.photo[-1]  # largest resolution
            suffix = ".jpg"
        elif content_type == ContentType.VOICE:
            media = msg.voice
            suffix = ".ogg"
        elif content_type == ContentType.VIDEO:
            media = msg.video
            suffix = ".mp4"
        else:  # DOCUMENT
            media = msg.document
            suffix = Path(media.file_name).suffix if media.file_name else ""

        file_path = await _download_file(media.file_id, context, suffix=suffix)
        capture = _build_capture(
            msg,
            content_type=content_type,
            file_path=file_path,
            file_id=media.file_id,
            caption=msg.caption,
        )
        result = await process_capture(capture)
        await _edit_with_result(reply, result)
    except Exception:
        logger.exception("Failed to process %s", label)
        await reply.edit_text(f"Failed to process {label}. Queued for retry.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages."""
    await _handle_media(update, context, ContentType.IMAGE, "image")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages."""
    await _handle_media(update, context, ContentType.VOICE, "voice memo")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document/file messages."""
    await _handle_media(update, context, ContentType.DOCUMENT, "document")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video messages."""
    await _handle_media(update, context, ContentType.VIDEO, "video")


def _build_capture(
    message: Message,
    *,
    content_type: ContentType,
    text: Optional[str] = None,
    url: Optional[str] = None,
    file_path: Optional[str] = None,
    file_id: Optional[str] = None,
    caption: Optional[str] = None,
) -> RawCapture:
    """Build a RawCapture from a Telegram message."""
    sender = str(message.from_user.id) if message.from_user else "unknown"
    source_chat = str(message.chat_id)

    # If message is forwarded, annotate the sender
    if message.forward_origin is not None:
        sender = f"fwd:{sender}"

    return RawCapture(
        content_type=content_type,
        text=text,
        url=url,
        file_path=file_path,
        file_id=file_id,
        caption=caption,
        sender=sender,
        source_chat=source_chat,
    )


async def _download_file(
    file_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    suffix: str = "",
) -> str:
    """Download a Telegram file to a temp directory and return the local path."""
    tg_file = await context.bot.get_file(file_id)
    tmp_dir = Path(tempfile.gettempdir()) / "secondbrain"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local_path = tmp_dir / f"{file_id}{suffix}"
    await tg_file.download_to_drive(custom_path=str(local_path))
    logger.info("Downloaded file %s -> %s", file_id, local_path)
    return str(local_path)


# Note type icons
_TYPE_ICONS = {
    "literature": "📄", "fleeting": "💡", "evergreen": "🌿",
    "recipe": "🍳", "reference": "📌", "person": "👤",
}

# Domain emojis for compact display
_DOMAIN_ICONS = {
    "data-engineering": "⚙️", "gen-ai": "🤖", "data-science": "📊",
    "computer-science": "💻", "job-search": "🔍", "fitness": "💪",
    "cooking": "🍳", "personal-finance": "💰", "wedding": "💍",
    "politics": "🏛️", "anime": "🎌", "ireland": "☘️", "india": "🇮🇳",
    "market-intelligence": "📈", "applied-ai": "🧠",
}


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown V1 special characters in dynamic content."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


async def _edit_with_retry(msg: Message, text: str, **kwargs) -> None:
    """Edit a message with retry on transient network errors."""
    for attempt in range(1, 4):
        try:
            await msg.edit_text(text, **kwargs)
            return
        except (NetworkError, TimedOut) as exc:
            if attempt == 3:
                raise
            await asyncio.sleep(2 * attempt)
            logger.warning("edit_text failed (attempt %d/3): %s — retrying", attempt, exc)
    raise RuntimeError("unreachable")


async def _edit_with_result(reply_message: Message, result: PipelineResult) -> None:
    """Edit the reply with a smart, concise, actionable response."""
    # Handle duplicate URL — not an error, just already saved
    if result.error == "duplicate" and result.note:
        note = result.note
        title = _escape_md(note.title)
        created = note.created_at.strftime('%Y-%m-%d') if hasattr(note.created_at, 'strftime') else str(note.created_at)[:10]
        url = note.source_url or ""
        await _edit_with_retry(
            reply_message,
            (
                f"Already in your brain: *{title}*\n"
                f"Saved on {created}\n\n"
                f"_To re-process:_ `/forget {url}` then send the link again"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if result.status == ProcessingStatus.DONE and result.note:
        note = result.note
        type_icon = _TYPE_ICONS.get(note.note_type.value, "📝")
        domain_icons = " ".join(
            _DOMAIN_ICONS.get(d, "📁") for d in note.domains[:3]
        )
        domains_str = " / ".join(d.replace("-", " ").title() for d in note.domains[:3])
        quality_stars = "★" * note.quality_score + "☆" * (5 - note.quality_score)
        safe_title = _escape_md(note.title)

        # Show which AI provider was used
        provider = "keyword"
        for tag in note.tags:
            if tag.startswith("ai/"):
                provider = tag.split("/", 1)[1]
                break
        provider_label = {"gemini": "Gemini", "groq": "Groq", "keyword": "Keyword"}.get(provider, provider.title())

        text = (
            f"{type_icon} *{safe_title}*\n"
            f"{domain_icons}  {domains_str}\n"
            f"Quality: {quality_stars}  ·  AI: {provider_label}\n"
            f"\n⚡ {result.processing_time_ms}ms"
        )

        if "status/needs-ai-review" in note.tags:
            text += (
                "\n\n⏳ *AI quota exhausted* — saved with keyword tags only.\n"
                "Will auto-reprocess at midnight UTC with full AI.\n"
                f"Or manually: `/reprocess {result.raw_id}`"
            )

        try:
            from src.bot.daily_digest import format_related_notes_alert
            related = await format_related_notes_alert(note.title, note.domains)
            if related:
                text += related
        except Exception:
            pass

        await _edit_with_retry(reply_message, text, parse_mode=ParseMode.MARKDOWN)
        return

    if result.status == ProcessingStatus.FAILED:
        error_msg = _escape_md(result.error or "Unknown error")
        if "come back tomorrow" in error_msg.lower() or "exhausted" in error_msg.lower():
            await _edit_with_retry(
                reply_message,
                f"⏳ *AI limit reached for today*\nContent saved with keyword-based tags.\n"
                f"Full AI categorization auto-runs at midnight UTC.\n"
                f"Or: `/reprocess {result.raw_id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await _edit_with_retry(
            reply_message,
            f"❌ Failed: {error_msg[:100]}\n`/reprocess {result.raw_id}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await _edit_with_retry(
        reply_message,
        f"Status: {result.status.value} | `/reprocess {result.raw_id}`",
        parse_mode=ParseMode.MARKDOWN,
    )
