"""Application entry point for the Second Brain Telegram bot."""

from __future__ import annotations

import asyncio
import logging
import re

from src.config.settings import get_settings
from src.pipeline.database import init_db
from src.pipeline.processor import initialize_pipeline


async def startup() -> None:
    """Initialise all subsystems and start the Telegram bot."""
    settings = get_settings()

    # Database
    await init_db(settings.db_path)

    # Pipeline (semaphore, etc.)
    await initialize_pipeline()

    # Credit tracker — loads today's API usage counts from DB
    try:
        from src.utils.credit_tracker import init_tracker
        init_tracker(settings.db_path)
    except Exception:
        pass

    # Start dashboard API server (background thread) — uses settings.api_bind_host/api_port
    try:
        from src.api.server import register_bot_loop, start_api_server
        # Register the running event loop BEFORE the server starts accepting
        # requests so the HTTP thread can safely schedule async DB operations.
        register_bot_loop(asyncio.get_event_loop())
        start_api_server()
    except Exception:
        logging.getLogger(__name__).warning("Dashboard API server failed to start")

    # Refuse to start the bot if no users are authorized — empty allow-list = deny everyone.
    if not settings.telegram_allowed_users:
        raise SystemExit(
            "Refusing to start: TELEGRAM_ALLOWED_USERS is empty. "
            "Set it in .env to a comma-separated list of your Telegram user IDs "
            "(e.g. TELEGRAM_ALLOWED_USERS=123456789)."
        )

    # Start bot (blocks until shutdown)
    from src.bot.bot import run_bot

    await run_bot()


class _TokenScrubFilter(logging.Filter):
    """Scrub Telegram bot token from httpx/httpcore log records.

    PTB embeds the token in every API URL path (``/bot<token>/...``).
    Even at WARNING level those URLs can appear in error messages.
    This filter replaces any occurrence of the literal token string
    (and the ``bot<token>`` prefix) with a redacted placeholder.
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        if token:
            # Match the full "bot<token>" URL segment AND the bare token.
            escaped = re.escape(token)
            self._pattern: re.Pattern[str] | None = re.compile(
                rf"bot{escaped}|{escaped}", re.IGNORECASE
            )
        else:
            self._pattern = None

    def filter(self, record: logging.LogRecord) -> bool:
        if self._pattern is None:
            return True
        try:
            record.msg = self._pattern.sub("[BOT_TOKEN]", str(record.msg))
            record.args = tuple(
                self._pattern.sub("[BOT_TOKEN]", str(a)) if isinstance(a, str) else a
                for a in (record.args or ())
            )
        except Exception:
            pass
        return True


def _setup_logging() -> None:
    """Configure root logger (stdout + rotating file) and install the
    bot-token scrub filter."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Rotating file log under data/ (the persisted volume on deploys) so
    # crashes on a headless server can be diagnosed after the fact.
    try:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        log_dir = Path("data") / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        ))
        logging.getLogger().addHandler(file_handler)
    except Exception:
        logging.getLogger(__name__).warning("File logging unavailable", exc_info=True)

    # Telegram's API embeds the bot token in every URL path. httpx logs the
    # full URL at INFO, which leaks the token into stdout and any log file.
    # Quieten the request logger; real errors still surface at WARNING+.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Install a scrub filter so the token never appears in any log record,
    # even at WARNING+ level (e.g., httpx error messages include the URL).
    try:
        from src.config.settings import get_settings
        token = get_settings().telegram_bot_token or ""
    except Exception:
        token = ""

    scrub = _TokenScrubFilter(token)
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).addFilter(scrub)


def main() -> None:
    """Configure logging and launch the async event loop."""
    _setup_logging()
    asyncio.run(startup())


if __name__ == "__main__":
    main()
