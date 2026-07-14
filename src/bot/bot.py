"""Telegram bot initialization and runner for the Second Brain app."""

from __future__ import annotations

import asyncio
import logging
import signal

from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.bot.handlers import (
    ask_command,
    credits_command,
    delete_command,
    forget_command,
    handle_document,
    handle_photo,
    handle_text,
    handle_video,
    handle_voice,
    lint_command,
    recent_command,
    reprocess_command,
    save_command,
    search_command,
    start_command,
    stats_command,
    status_command,
    tag_command,
)
from src.config.settings import get_settings

logger = logging.getLogger(__name__)


def _build_application() -> Application:
    """Create and configure the Telegram bot Application."""
    settings = get_settings()
    app = Application.builder().token(settings.telegram_bot_token).build()

    # --- Command handlers (evaluated first) ---
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("save", save_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("credits", credits_command))
    app.add_handler(CommandHandler("reprocess", reprocess_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("tag", tag_command))
    app.add_handler(CommandHandler("recent", recent_command))
    app.add_handler(CommandHandler("lint", lint_command))

    # --- Content handlers (order matters — more specific first) ---
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Schedule daily digest (8:00 AM UTC — "resurface" a random note)
    try:
        from src.bot.daily_digest import schedule_daily_digest
        schedule_daily_digest(app)
    except Exception:
        logger.warning("Daily digest scheduling failed — continuing without it")

    # Error handler — log network errors at WARNING, everything else at ERROR
    async def _error_handler(update: object, context) -> None:  # type: ignore[type-arg]
        exc = context.error
        if isinstance(exc, (NetworkError, TimedOut)):
            logger.warning("Telegram network error (transient): %s", exc)
        else:
            logger.exception("Unhandled bot exception", exc_info=exc)

    app.add_error_handler(_error_handler)

    # Register the all-providers-exhausted alert — fires (rate-limited to
    # once/hour) whenever call_ai() exhausts Gemini, Groq, and Ollama.
    try:
        from src.categorizer.providers import register_exhaustion_callback

        async def _on_providers_exhausted(_message: str) -> None:
            text = (
                "⚠️ All AI providers exhausted — notes are falling back to "
                "keyword categorization until quotas reset (08:00 UTC for Gemini)."
            )
            for uid in settings.telegram_allowed_users:
                try:
                    await app.bot.send_message(chat_id=uid, text=text)
                except Exception:
                    logger.exception("Failed to send provider-exhaustion alert to %s", uid)

        register_exhaustion_callback(_on_providers_exhausted)
    except Exception:
        logger.warning("Could not register provider-exhaustion callback — continuing without it")

    logger.info("Bot handlers registered successfully")
    return app


async def run_bot() -> None:
    """Start the Telegram bot (long-polling mode).

    Call this from an async entry point::

        import asyncio
        from src.bot.bot import run_bot
        asyncio.run(run_bot())
    """
    logger.info("Starting Second Brain Telegram bot...")
    app = _build_application()

    # Retry initialization — a transient ConnectError at startup shouldn't crash the process
    for attempt in range(1, 6):
        try:
            await app.initialize()
            break
        except Exception as exc:
            if attempt == 5:
                raise
            wait = 5 * attempt
            logger.warning(
                "Bot initialization failed (attempt %d/5): %s — retrying in %ds",
                attempt, exc, wait,
            )
            await asyncio.sleep(wait)

    # API key health check — best-effort, must never block startup. Silent
    # when every key is fine; alerts allowed users listing bad slots otherwise.
    try:
        from src.categorizer.providers import validate_api_keys

        settings = get_settings()
        health = await validate_api_keys(settings)
        bad_slots = [
            entry["key_slot"]
            for entries in health.values()
            for entry in entries
            if entry["status"] in ("invalid", "unreachable")
        ]
        if bad_slots and settings.telegram_allowed_users:
            text = (
                "⚠️ API key health check found problems at startup:\n"
                + "\n".join(f"  • {slot}" for slot in bad_slots)
            )
            for uid in settings.telegram_allowed_users:
                try:
                    await app.bot.send_message(chat_id=uid, text=text)
                except Exception:
                    logger.exception("Failed to send key-health alert to %s", uid)
    except Exception:
        logger.exception("API key health check failed at startup — continuing")

    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot is now polling for updates — press Ctrl+C to stop")

    # Block until interrupted
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler — fall back
            signal.signal(sig, lambda *_: stop_event.set())

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down bot...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Bot shut down cleanly")
