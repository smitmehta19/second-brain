"""Daily digest + weekly summary + related notes + vault health.

Features:
- Daily digest at 8 AM UTC: stats + smart resurfaced note
- Weekly summary on Sundays: themes, capture count, focus suggestions
- Related notes alert: triggered after each new capture
- Vault health score: shown in /stats and dashboard
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone, timedelta
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault Health Score
# ---------------------------------------------------------------------------

async def calculate_vault_health() -> dict:
    """Calculate vault health score (0-100) based on note quality.

    Scoring:
    - Start at 100
    - -3 per note with quality_score=1 (low quality)
    - -2 per note with no domains
    - -1 per note with no summary
    - Cap at 0 minimum
    """
    try:
        from src.pipeline.database import _get_db

        db = _get_db()
        cursor = await db.execute(
            "SELECT quality_score, domains, summary FROM notes"
        )
        rows = await cursor.fetchall()

        if not rows:
            return {"score": 100, "total": 0, "issues": []}

        score = 100
        issues = []
        total = len(rows)
        low_quality = 0
        no_domains = 0
        no_summary = 0

        for row in rows:
            q = row["quality_score"] or 3
            if q <= 1:
                low_quality += 1
            domains = json.loads(row["domains"]) if row["domains"] else []
            if not domains:
                no_domains += 1
            if not row["summary"]:
                no_summary += 1

        score -= low_quality * 3
        score -= no_domains * 2
        score -= no_summary * 1
        score = max(0, min(100, score))

        if low_quality:
            issues.append(f"{low_quality} low-quality notes")
        if no_domains:
            issues.append(f"{no_domains} uncategorized notes")
        if no_summary:
            issues.append(f"{no_summary} notes without summary")

        return {"score": score, "total": total, "issues": issues}

    except Exception:
        return {"score": 100, "total": 0, "issues": []}


# ---------------------------------------------------------------------------
# Related Notes
# ---------------------------------------------------------------------------

async def find_related_notes(title: str, domains: list[str], limit: int = 3) -> list[dict]:
    """Find notes related to a newly saved note.

    Matches by:
    1. Same domain (strongest signal)
    2. Title word overlap
    """
    try:
        from src.pipeline.database import _get_db

        db = _get_db()

        # Find notes in the same domain
        related = []
        for domain in domains[:2]:
            cursor = await db.execute(
                "SELECT id, title, domains FROM notes WHERE domains LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{domain}%", limit + 5),
            )
            rows = await cursor.fetchall()
            for row in rows:
                if row["title"] != title and row["id"] not in [r["id"] for r in related]:
                    related.append(dict(row))

        # Score by title word overlap
        title_words = set(title.lower().split())
        scored = []
        for note in related:
            note_words = set(note["title"].lower().split())
            overlap = len(title_words & note_words - {"the", "a", "an", "of", "in", "to", "and", "for", "is", "on"})
            scored.append((note, overlap))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:limit]]

    except Exception:
        return []


async def format_related_notes_alert(title: str, domains: list[str]) -> str | None:
    """Generate a "related notes" message for the user after saving a new note."""
    related = await find_related_notes(title, domains)
    if not related:
        return None

    lines = [f"\n💡 *Related in your palace:*"]
    for note in related[:3]:
        lines.append(f"  • _{note['title']}_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smart Resurface (weighted, not random)
# ---------------------------------------------------------------------------

async def get_smart_resurface() -> dict | None:
    """Pick a note to resurface — weighted by quality and diversity.

    Prefers:
    - Higher quality notes (more useful to review)
    - Notes from domains you haven't seen recently
    - Older notes (more likely forgotten)
    """
    try:
        from src.pipeline.database import _get_db

        db = _get_db()
        cursor = await db.execute(
            "SELECT id, title, domains, source_url, summary, quality_score, created_at "
            "FROM notes ORDER BY created_at ASC LIMIT 50"
        )
        rows = await cursor.fetchall()
        if not rows:
            return None

        # Weight by quality (higher = more likely to resurface)
        weighted = []
        for row in rows:
            q = row["quality_score"] or 3
            weight = q * q  # quadratic — quality 5 is 25x more likely than quality 1
            weighted.extend([dict(row)] * weight)

        return random.choice(weighted) if weighted else dict(rows[0])

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Daily Digest
# ---------------------------------------------------------------------------

async def send_daily_digest(bot, chat_id: int) -> None:
    """Send the daily Mind Palace digest."""
    try:
        from src.pipeline.database import get_stats
        from telegram.constants import ParseMode

        stats = await get_stats()
        total = stats.get("total_notes", 0)

        # Smart resurface
        resurfaced = await get_smart_resurface()

        # Vault health
        health = await calculate_vault_health()

        now = datetime.now(timezone.utc)
        greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"

        health_emoji = "🟢" if health["score"] >= 80 else "🟡" if health["score"] >= 50 else "🔴"

        lines = [
            f"🏛️ *{greeting}!*\n",
            f"*Mind Palace Stats*",
            f"  📝 Total notes: *{total}*",
            f"  {health_emoji} Vault health: *{health['score']}/100*",
        ]

        if health["issues"]:
            lines.append(f"  ⚠️ _{', '.join(health['issues'][:2])}_")

        # Resurface
        if resurfaced:
            title = resurfaced["title"]
            domains = json.loads(resurfaced["domains"]) if resurfaced["domains"] else []
            domain_str = " / ".join(d.replace("-", " ").title() for d in domains[:2])
            url = resurfaced.get("source_url", "")

            lines.append(f"\n💡 *Resurface — Do you remember this?*")
            lines.append(f"_{title}_")
            if domain_str:
                lines.append(f"📁 {domain_str}")
            if resurfaced.get("summary"):
                lines.append(f"_{resurfaced['summary'][:150]}_")
            if url:
                lines.append(f"[Open source]({url})")

        # AI usage summary — best-effort, never fails the digest.
        try:
            from src.utils.credit_tracker import get_usage, get_call_count
            from src.categorizer.providers import get_exhausted_slots

            usage = get_usage()
            gemini_calls = usage.get("gemini", {}).get("requests_used", 0)
            groq_calls = usage.get("groq", {}).get("requests_used", 0)
            ollama_calls = get_call_count("ollama")

            lines.append(
                f"\n🔑 *AI usage yesterday/today:* gemini {gemini_calls} calls, "
                f"groq {groq_calls}, ollama {ollama_calls}"
            )

            exhausted_slots = get_exhausted_slots()
            if exhausted_slots:
                lines.append(f"⛔ exhausted: {', '.join(exhausted_slots)}")
        except Exception:
            logger.debug("Daily digest: AI usage summary failed", exc_info=True)

        lines.append(f"\n_Dump anything to grow your palace!_")

        await bot.send_message(
            chat_id=chat_id, text="\n".join(lines),
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
        )
        logger.info("Daily digest sent to chat %d", chat_id)

    except Exception:
        logger.exception("Failed to send daily digest")


# ---------------------------------------------------------------------------
# Weekly Summary (Sundays)
# ---------------------------------------------------------------------------

async def send_weekly_summary(bot, chat_id: int) -> None:
    """Send the weekly Mind Palace summary on Sundays."""
    try:
        from src.pipeline.database import _get_db
        from telegram.constants import ParseMode

        db = _get_db()

        # Get this week's notes
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cursor = await db.execute(
            "SELECT title, domains, note_type, quality_score FROM notes WHERE created_at >= ?",
            (week_ago,),
        )
        rows = await cursor.fetchall()

        if not rows:
            await bot.send_message(
                chat_id=chat_id,
                text="🏛️ *Weekly Summary*\n\nQuiet week — no new captures. Send me something!",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Count by domain
        domain_counts = Counter()
        type_counts = Counter()
        total_quality = 0

        for row in rows:
            domains = json.loads(row["domains"]) if row["domains"] else []
            for d in domains:
                domain_counts[d] += 1
            type_counts[row["note_type"]] += 1
            total_quality += (row["quality_score"] or 3)

        avg_quality = round(total_quality / len(rows), 1) if rows else 0
        top_domains = domain_counts.most_common(3)

        # Vault health
        health = await calculate_vault_health()
        health_emoji = "🟢" if health["score"] >= 80 else "🟡" if health["score"] >= 50 else "🔴"

        lines = [
            f"🏛️ *Weekly Summary*\n",
            f"📊 *This week:* {len(rows)} captures",
            f"⭐ Average quality: {avg_quality}/5",
            f"{health_emoji} Vault health: {health['score']}/100\n",
        ]

        if top_domains:
            lines.append("*Top domains this week:*")
            for domain, count in top_domains:
                display = domain.replace("-", " ").title()
                lines.append(f"  • {display}: {count} notes")

        if type_counts:
            lines.append("\n*By type:*")
            type_icons = {"literature": "📄", "fleeting": "💡", "evergreen": "🌿", "recipe": "🍳", "reference": "📌"}
            for ntype, count in type_counts.most_common():
                icon = type_icons.get(ntype, "📝")
                lines.append(f"  {icon} {ntype}: {count}")

        # Suggest focus for next week
        if top_domains:
            weakest = domain_counts.most_common()[-1] if len(domain_counts) > 1 else None
            if weakest and weakest[1] <= 1:
                lines.append(f"\n🎯 *Suggestion:* Explore more about _{weakest[0].replace('-', ' ').title()}_")

        # Notion declutter: list auto-archive candidates so nothing vanishes silently.
        try:
            from src.config.settings import get_settings as _gs
            _settings = _gs()
            _days = _settings.auto_archive_days if _settings.auto_archive_days > 0 else 30
            stale = await _fetch_stale_published_notes(_days)
            if stale:
                verb = "will be auto-archived" if _settings.auto_archive_days > 0 else "are archive candidates"
                lines.append(f"\n🗄️ *Notion declutter:* {len(stale)} notes untouched {_days}+ days {verb}:")
                for n in stale[:5]:
                    lines.append(f"  • {n['title'][:60]}")
                if len(stale) > 5:
                    lines.append(f"  … and {len(stale) - 5} more")
        except Exception:
            logger.debug("Weekly summary: archive-candidate lookup failed", exc_info=True)

        lines.append(f"\n_Keep building your Mind Palace!_")

        await bot.send_message(
            chat_id=chat_id, text="\n".join(lines),
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
        )
        logger.info("Weekly summary sent to chat %d", chat_id)

    except Exception:
        logger.exception("Failed to send weekly summary")


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

async def auto_reprocess_pending(bot, chat_id: int) -> None:
    """Reprocess notes that were saved with keyword fallback (AI was exhausted).

    Runs at midnight UTC when AI provider limits reset.
    """
    try:
        from src.pipeline.database import _get_db
        from src.pipeline.processor import reprocess_capture
        from telegram.constants import ParseMode

        db = _get_db()
        cursor = await db.execute(
            "SELECT n.capture_id, n.title FROM notes n "
            "WHERE n.tags LIKE '%needs-ai-review%' "
            "ORDER BY n.created_at DESC LIMIT 10"
        )
        rows = await cursor.fetchall()

        if not rows:
            return

        logger.info("Auto-reprocessing %d notes that need AI review", len(rows))
        await bot.send_message(
            chat_id=chat_id,
            text=f"🔄 *Midnight reprocess* — retrying {len(rows)} notes with AI...",
            parse_mode=ParseMode.MARKDOWN,
        )

        success = 0
        failed = 0
        for row in rows:
            try:
                result = await reprocess_capture(row["capture_id"])
                if result.status.value == "done" and result.note:
                    if "status/needs-ai-review" not in result.note.tags:
                        success += 1
                    else:
                        failed += 1
                else:
                    failed += 1
            except Exception:
                logger.exception("Auto-reprocess failed for %s", row["capture_id"])
                failed += 1

        if success > 0 or failed > 0:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ *Midnight reprocess complete*\n"
                    f"  AI upgraded: {success}\n"
                    f"  Still pending: {failed}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )

    except Exception:
        logger.exception("Auto-reprocess job failed")


# ---------------------------------------------------------------------------
# Notion Archive Reconcile
# ---------------------------------------------------------------------------

# Sidecar file that persists the set of archived Notion page IDs already
# processed, so we only delete locally once per page.
_RECONCILE_STATE_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "notion_reconcile_state.json"
)


def _load_reconcile_state() -> set[str]:
    """Load previously-seen archived page IDs from the sidecar JSON."""
    try:
        if _RECONCILE_STATE_FILE.exists():
            data = json.loads(_RECONCILE_STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("last_seen_archived_ids", []))
    except Exception:
        pass
    return set()


def _save_reconcile_state(seen_ids: set[str]) -> None:
    """Persist the set of archived page IDs atomically."""
    _RECONCILE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _RECONCILE_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"last_seen_archived_ids": sorted(seen_ids)}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(_RECONCILE_STATE_FILE)


async def notion_archive_reconcile() -> None:
    """Poll Notion for archived pages and hard-delete matching local notes.

    Steps:
    1. Query the Notion database(s) for all pages; filter client-side on
       ``archived == True`` (Notion's REST API exposes ``archived`` as a
       top-level field on each page object, not as a filterable property).
    2. Diff against previously-seen archived IDs to find *newly* archived pages.
    3. For each newly-archived page whose notion_page_id appears in notes.json,
       delete the note from SQLite and remove it from notes.json.
    4. Persist the updated seen-ID set to the sidecar file.
    """
    from src.config.settings import get_settings

    settings = get_settings()

    if not settings.notion_api_key:
        logger.debug("Notion not configured — skipping archive reconcile")
        return

    database_id = settings.notion_inbox_database_id or settings.notion_resources_database_id
    if not database_id:
        logger.debug("No Notion database ID — skipping archive reconcile")
        return

    try:
        from notion_client import AsyncClient
    except ImportError:
        logger.warning("notion-client not installed — skipping archive reconcile")
        return

    # ── 1. Fetch all archived pages from Notion (paginate) ──────────────────
    # notion-client 3.x: databases.query was removed; query data sources instead.
    # A Notion database has one or more data sources (typically one); we retrieve
    # the DB to discover them, then paginate each.
    notion = AsyncClient(auth=settings.notion_api_key)
    archived_page_ids: set[str] = set()
    try:
        db_meta = await notion.databases.retrieve(database_id=database_id)
        data_source_ids = [ds["id"] for ds in db_meta.get("data_sources", [])]
        if not data_source_ids:
            logger.warning("Notion archive reconcile: database %s has no data sources", database_id)
            return

        for ds_id in data_source_ids:
            start_cursor = None
            while True:
                kwargs: dict = {
                    "data_source_id": ds_id,
                    # ``archived`` is a top-level page field, not a queryable
                    # property — we fetch all pages and filter client-side.
                    "page_size": 100,
                }
                if start_cursor:
                    kwargs["start_cursor"] = start_cursor

                result = await notion.data_sources.query(**kwargs)
                for page in result.get("results", []):
                    if page.get("archived"):
                        archived_page_ids.add(page["id"])

                if not result.get("has_more"):
                    break
                start_cursor = result.get("next_cursor")

    except Exception as exc:
        logger.warning("Notion archive reconcile: query failed — %s", exc)
        return

    if not archived_page_ids:
        logger.debug("Notion archive reconcile: no archived pages found")
        _save_reconcile_state(archived_page_ids)
        return

    # ── 2. Diff against previously-seen IDs ─────────────────────────────────
    previously_seen = _load_reconcile_state()
    newly_archived = archived_page_ids - previously_seen

    if not newly_archived:
        logger.debug(
            "Notion archive reconcile: %d archived pages, none new",
            len(archived_page_ids),
        )
        _save_reconcile_state(archived_page_ids)
        return

    logger.info(
        "Notion archive reconcile: %d newly archived pages to process",
        len(newly_archived),
    )

    # ── 3. Find matching local notes (SQLite is authoritative) and delete ────
    from src.pipeline.database import _get_db, delete_note

    try:
        db = _get_db()
        ids = tuple(newly_archived)
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"SELECT id, title FROM notes WHERE notion_page_id IN ({placeholders})",
            ids,
        )
        rows = await cursor.fetchall()
    except Exception as exc:
        logger.warning("Notion archive reconcile: DB lookup failed — %s", exc)
        _save_reconcile_state(archived_page_ids)
        return

    deleted_any = False
    for row in rows:
        try:
            await delete_note(row["id"])
            deleted_any = True
            logger.info(
                "Notion archive reconcile: deleted note %s ('%s') — Notion page archived",
                row["id"], row["title"],
            )
        except Exception as exc:
            logger.warning(
                "Notion archive reconcile: SQLite delete failed for note %s ('%s'): %s — skipping",
                row["id"], row["title"], exc,
            )

    # Refresh the notes.json cache so static views drop the deleted notes.
    if deleted_any:
        _regen_notes_cache()

    # ── 4. Persist updated seen set ──────────────────────────────────────────
    _save_reconcile_state(archived_page_ids)


def _regen_notes_cache() -> None:
    """Regenerate docs/notes.json from the DB (non-blocking subprocess)."""
    try:
        import subprocess
        import sys
        from pathlib import Path as _Path
        project_root = _Path(__file__).resolve().parent.parent.parent
        subprocess.Popen(
            [sys.executable, "-m", "src.search.export_json"],
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Non-critical — cache regen only


async def _fetch_stale_published_notes(days: int) -> list[dict]:
    """Notes with a Notion page whose last touch (review, else creation) is
    older than *days* days. These are auto-archive candidates."""
    from src.pipeline.database import _get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = _get_db()
    cursor = await db.execute(
        "SELECT id, title, notion_page_id FROM notes "
        "WHERE notion_page_id IS NOT NULL AND notion_page_id != '' "
        "AND COALESCE(NULLIF(reviewed_at, ''), created_at) < ? "
        "ORDER BY created_at ASC",
        (cutoff,),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def auto_archive_stale_notion_pages() -> None:
    """Archive Notion pages for notes untouched for AUTO_ARCHIVE_DAYS days.

    Anti-clutter policy: the local note is KEPT (the brain remembers
    everything); only the Notion page is archived. The archived page id is
    added to the reconcile seen-state BEFORE archiving so the 5-minute
    reconcile poll does not mistake it for a user-initiated Notion delete
    and hard-delete the local note.
    """
    from src.config.settings import get_settings

    settings = get_settings()
    days = settings.auto_archive_days
    if days <= 0 or not settings.notion_api_key:
        return

    try:
        stale = await _fetch_stale_published_notes(days)
    except Exception:
        logger.exception("Auto-archive: could not query stale notes")
        return
    if not stale:
        logger.debug("Auto-archive: nothing older than %d days", days)
        return

    from src.integrations.notion_sync import archive_page

    # Mark as self-archived first (see docstring).
    seen = _load_reconcile_state()
    seen.update(n["notion_page_id"] for n in stale)
    _save_reconcile_state(seen)

    archived = 0
    for note in stale:
        if await archive_page(note["notion_page_id"], settings):
            archived += 1
            logger.info(
                "Auto-archived Notion page for note %s ('%s') — untouched %d+ days",
                note["id"], note["title"], days,
            )
    logger.info("Auto-archive: archived %d/%d stale Notion pages", archived, len(stale))


def schedule_daily_digest(app) -> None:
    """Schedule daily digest, weekly summary, and midnight auto-reprocess."""
    from src.config.settings import get_settings
    from datetime import time as dt_time

    settings = get_settings()
    if not settings.telegram_allowed_users:
        logger.info("No allowed users — skipping digest scheduling")
        return

    async def _daily(context):
        for uid in settings.telegram_allowed_users:
            await send_daily_digest(context.bot, uid)

    async def _weekly(context):
        if datetime.now(timezone.utc).weekday() == 6:
            for uid in settings.telegram_allowed_users:
                await send_weekly_summary(context.bot, uid)

    async def _midnight_reprocess(context):
        for uid in settings.telegram_allowed_users:
            await auto_reprocess_pending(context.bot, uid)

    app.job_queue.run_daily(
        _daily, time=dt_time(hour=8, minute=0, tzinfo=timezone.utc), name="daily_digest",
    )
    app.job_queue.run_daily(
        _weekly, time=dt_time(hour=19, minute=0, tzinfo=timezone.utc), name="weekly_summary",
    )
    app.job_queue.run_daily(
        _midnight_reprocess, time=dt_time(hour=0, minute=5, tzinfo=timezone.utc),
        name="midnight_reprocess",
    )

    if settings.auto_archive_days > 0:
        async def _auto_archive(context):
            await auto_archive_stale_notion_pages()

        app.job_queue.run_daily(
            _auto_archive, time=dt_time(hour=3, minute=0, tzinfo=timezone.utc),
            name="auto_archive_stale",
        )
        logger.info(
            "Scheduled: auto-archive of Notion pages untouched %d+ days (03:00 UTC)",
            settings.auto_archive_days,
        )

    reconcile_interval = settings.notion_reconcile_interval_min
    if reconcile_interval > 0:
        async def _notion_reconcile(context):  # noqa: E306
            await notion_archive_reconcile()

        app.job_queue.run_repeating(
            _notion_reconcile,
            interval=reconcile_interval * 60,
            first=60,  # First run 60 s after startup to avoid racing init
            name="notion_archive_reconcile",
        )
        logger.info(
            "Scheduled: daily digest 08:00 UTC, weekly Sunday 19:00 UTC, "
            "midnight reprocess 00:05 UTC, Notion archive reconcile every %d min",
            reconcile_interval,
        )
    else:
        logger.info(
            "Scheduled: daily digest 08:00 UTC, weekly Sunday 19:00 UTC, "
            "midnight reprocess 00:05 UTC (Notion reconcile disabled — "
            "NOTION_RECONCILE_INTERVAL_MIN=%d)",
            reconcile_interval,
        )
