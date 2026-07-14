"""Lightweight API server for the Second Brain dashboard.

Runs alongside the Telegram bot on the same Oracle Cloud VM.
Serves notes.json + handles delete/search actions from the dashboard.

SQLite (src/pipeline/database.py) is the source of truth for every endpoint;
docs/notes.json is only a generated cache (regenerated after each mutation)
for static consumers like the GitHub Pages mindmap.

Endpoints:
    GET  /healthz                         — liveness (no auth)
    GET  /api/notes                       — all notes as JSON
    GET  /api/notes?q=search              — search notes
    DELETE /api/notes/<id>                — delete a note (also "trash" in review)
    POST /api/notes/<id>/review           — mark reviewed (keep) with buckets[];
                                            triggers deferred Notion publish
    POST /api/notes/<id>/bucket           — set bucket(s) for a note
    POST /api/notes/<id>/edit             — edit title/summary/why_keep/tags/key_takeaways
    GET  /api/buckets                     — canonical + custom buckets list
    POST /api/buckets                     — create a new custom bucket
    GET  /api/stats                       — vault statistics
    GET  /api/credits                     — AI provider credit usage today
    GET  /api/review/queue                — notes for daily review queue (last 7d, unreviewed)
    GET  /                                — serves docs/index.html
    GET  /mindmap.html                    — serves docs/mindmap.html
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Optional
from urllib.parse import parse_qs, urlparse

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

# Module-level reference to the bot's running event loop.
# Registered via register_bot_loop() before the HTTP server accepts requests.
_bot_loop: Optional[asyncio.AbstractEventLoop] = None


def register_bot_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store a reference to the bot's event loop for use by the HTTP thread."""
    global _bot_loop  # noqa: PLW0603
    _bot_loop = loop


_DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _run_db(coro, timeout: float = 15.0):
    """Run an async DB coroutine from the HTTP thread and return its result.

    Schedules onto the bot's event loop (where the aiosqlite connection
    lives). Falls back to a fresh loop only if the bot loop is unavailable
    (e.g. during tests).
    """
    loop = _bot_loop
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    return asyncio.run(coro)


def _fire_and_forget(coro, what: str) -> None:
    """Schedule an async side-effect (Notion sync etc.) without blocking the
    HTTP response. Failures are logged by the coroutine itself."""
    loop = _bot_loop
    try:
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            asyncio.run(coro)
    except Exception:
        logger.warning("Failed to schedule %s", what)


def _regen_notes_cache() -> None:
    """Regenerate docs/notes.json from the DB (non-blocking subprocess).

    The dashboard reads live from /api/*; this cache only feeds static
    consumers (GitHub Pages mindmap, atlas prebuilds)."""
    try:
        import subprocess
        import sys
        project_root = Path(__file__).resolve().parent.parent.parent
        subprocess.Popen(
            [sys.executable, "-m", "src.search.export_json"],
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Non-critical — cache regen only


class DashboardHandler(SimpleHTTPRequestHandler):
    """Handles both static file serving and API endpoints."""

    def __init__(self, *args, db_funcs=None, **kwargs):
        self._db = db_funcs or {}
        self._settings = get_settings()
        super().__init__(*args, directory=str(_DOCS_DIR), **kwargs)

    def _authorized(self) -> bool:
        """Constant-time check of the Bearer token against settings.dashboard_token.

        If DASHBOARD_TOKEN is empty and the server is bound to localhost only,
        unauthenticated access is permitted for local development convenience.
        """
        token = self._settings.dashboard_token
        bind_host = self._settings.api_bind_host
        # Allow unauthenticated access only when running locally without a token.
        if not token:
            loopback = ("127.0.0.1", "::1", "localhost")
            return bind_host in loopback
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip(), token)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/healthz":
            self._handle_healthz()
            return

        if path.startswith("/api/"):
            if not self._authorized():
                self._send_json({"error": "Unauthorized"}, 401)
                return
            if path == "/api/notes":
                self._handle_get_notes(query)
            elif path == "/api/stats":
                self._handle_get_stats()
            elif path == "/api/credits":
                self._handle_get_credits()
            elif path == "/api/buckets":
                self._handle_get_buckets()
            elif path == "/api/review/queue":
                self._handle_get_review_queue()
            else:
                self._send_json({"error": "Not found"}, 404)
        else:
            # Serve static files from docs/
            super().do_GET()

    def do_DELETE(self):
        if not self._authorized():
            self._send_json({"error": "Unauthorized"}, 401)
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/notes/"):
            note_id = path.split("/api/notes/")[1]
            self._handle_delete_note(note_id)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            self._send_json({"error": "Unauthorized"}, 401)
            return

        parsed = urlparse(self.path)
        path = parsed.path

        # Read body
        length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            body = {}

        if re.fullmatch(r"/api/notes/[A-Za-z0-9_-]{1,64}/review", path):
            note_id = path.split("/")[3]
            self._handle_post_review(note_id, body)
        elif re.fullmatch(r"/api/notes/[A-Za-z0-9_-]{1,64}/bucket", path):
            note_id = path.split("/")[3]
            self._handle_post_bucket(note_id, body)
        elif re.fullmatch(r"/api/notes/[A-Za-z0-9_-]{1,64}/edit", path):
            note_id = path.split("/")[3]
            self._handle_post_edit(note_id, body)
        elif path == "/api/buckets":
            self._handle_post_bucket_create(body)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self):
        # Exact-match origin; never wildcard, never with credentials.
        self.send_header("Access-Control-Allow-Origin", self._settings.dashboard_origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _handle_healthz(self):
        """Liveness probe (no auth): verifies the DB is reachable."""
        try:
            from src.pipeline.database import get_stats as db_stats
            stats = _run_db(db_stats(), timeout=5)
            self._send_json({"status": "ok", "notes": stats.get("total_notes", 0)})
        except Exception:
            logger.exception("Health check failed")
            self._send_json({"status": "degraded"}, 503)

    @staticmethod
    def _hydrate_buckets(notes: list[dict]) -> list[dict]:
        """Ensure every note has a non-empty `buckets` list (["DUMP"] fallback)."""
        for n in notes:
            buckets = n.get("buckets")
            if not isinstance(buckets, list) or not buckets:
                legacy = n.get("bucket")
                n["buckets"] = [str(legacy).upper()] if legacy else ["DUMP"]
        return notes

    def _handle_get_notes(self, query):
        """Return all notes from SQLite, optionally filtered by search query."""
        try:
            from src.pipeline.database import fetch_dashboard_notes
            notes = self._hydrate_buckets(_run_db(fetch_dashboard_notes()))

            q = query.get("q", [""])[0].lower()
            if q:
                notes = [
                    n for n in notes
                    if q in n.get("title", "").lower()
                    or q in n.get("summary", "").lower()
                    or q in " ".join(n.get("domains", [])).lower()
                    or q in " ".join(n.get("tags", [])).lower()
                ]

            self._send_json(notes)
        except Exception as exc:
            logger.exception("Failed to get notes")
            self._send_json({"error": str(exc)}, 500)

    def _handle_get_stats(self):
        """Return vault statistics from SQLite."""
        try:
            from src.pipeline.database import fetch_dashboard_notes
            notes = _run_db(fetch_dashboard_notes())

            domains = {}
            types = {}
            for n in notes:
                for d in n.get("domains", []):
                    domains[d] = domains.get(d, 0) + 1
                t = n.get("note_type", "unknown")
                types[t] = types.get(t, 0) + 1

            self._send_json({
                "total": len(notes),
                "domains": domains,
                "types": types,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _handle_get_credits(self):
        """Return current AI provider credit usage."""
        try:
            from src.utils.credit_tracker import get_usage
            self._send_json(get_usage())
        except ImportError:
            self._send_json({"error": "Credit tracker not available"}, 503)
        except Exception as exc:
            logger.exception("Failed to get credit usage")
            self._send_json({"error": str(exc)}, 500)

    def _handle_delete_note(self, note_id: str):
        """Delete a note from SQLite (authoritative), then archive in Notion."""
        # Validate id shape before touching anything (defense-in-depth — IDs are hex_12 in DB).
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", note_id):
            self._send_json({"error": "Invalid note id"}, 400)
            return

        try:
            from src.pipeline.database import delete_note, get_note_row

            row = _run_db(get_note_row(note_id))
            if row is None:
                self._send_json({"error": "Note not found"}, 404)
                return
            notion_page_id: Optional[str] = row.get("notion_page_id") or None

            deleted = _run_db(delete_note(note_id))
            if not deleted:
                self._send_json({"error": "Note not found"}, 404)
                return

            logger.info("Deleted note %s via API", note_id)

            # Archive in Notion (best-effort — failure must NOT roll back the local delete).
            if notion_page_id:
                from src.integrations.notion_sync import archive_page
                _fire_and_forget(
                    archive_page(notion_page_id, self._settings),
                    f"Notion archive for {note_id}",
                )

            _regen_notes_cache()
            self._send_json({"deleted": note_id})

        except Exception as exc:
            logger.exception("Failed to delete note %s", note_id)
            self._send_json({"error": str(exc)}, 500)

    # ── Review Queue ────────────────────────────────────────────────────

    def _handle_get_review_queue(self):
        """Return unreviewed notes from the last 7 days (SQLite-backed)."""
        try:
            from src.pipeline.database import fetch_review_queue
            queue = self._hydrate_buckets(_run_db(fetch_review_queue(days=7)))
            self._send_json(queue)
        except Exception as exc:
            logger.exception("Failed to get review queue")
            self._send_json({"error": str(exc)}, 500)

    # ── Bucket helpers ─────────────────────────────────────────────────

    @staticmethod
    def _normalize_buckets_input(body: dict) -> list[str]:
        """Accept {buckets:[...]} OR legacy {bucket:"..."}; return list[str].

        Strips whitespace, uppercases, de-duplicates while preserving order.
        Raises ValueError on malformed shapes.
        """
        raw = body.get("buckets")
        if raw is None and body.get("bucket"):
            raw = [body.get("bucket")]
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValueError("'buckets' must be a list of strings")
        out: list[str] = []
        seen: set = set()
        for b in raw:
            if not isinstance(b, str):
                raise ValueError("each bucket must be a string")
            v = b.strip().upper()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _handle_get_buckets(self):
        """Return canonical + custom buckets as a unified list."""
        try:
            from src.config.buckets import get_all_buckets
            self._send_json(get_all_buckets())
        except Exception as exc:
            logger.exception("Failed to list buckets")
            self._send_json({"error": str(exc)}, 500)

    def _handle_post_bucket_create(self, body: dict):
        """Create a new user-defined custom bucket.

        Request body: {"name": "MYBUCKET", "label": "Optional label"}.
        Returns the created record on success.
        """
        try:
            from src.config.buckets import add_custom_bucket
            name = body.get("name", "")
            label = body.get("label") or None
            try:
                record = add_custom_bucket(name, label)
            except ValueError as ve:
                self._send_json({"error": str(ve)}, 400)
                return
            logger.info("Custom bucket created: %s", record["name"])
            self._send_json(record, status=201)
        except Exception as exc:
            logger.exception("Failed to create custom bucket")
            self._send_json({"error": str(exc)}, 500)

    def _handle_post_bucket(self, note_id: str, body: dict):
        """Set bucket(s) for a note.

        Accepts:
            {"buckets": ["CAREER","WATCH-LONG"]}     — preferred
            {"bucket":  "CAREER"}                    — legacy single
        At least one valid bucket is required.  Writes both `buckets` (list)
        and `bucket` (first element, for back-compat readers).
        """
        from src.config.buckets import is_valid_bucket

        try:
            buckets = self._normalize_buckets_input(body)
        except ValueError as ve:
            self._send_json({"error": str(ve)}, 400)
            return
        if not buckets:
            self._send_json({"error": "At least one bucket is required"}, 400)
            return
        invalid = [b for b in buckets if not is_valid_bucket(b)]
        if invalid:
            self._send_json(
                {"error": f"Unknown bucket(s): {', '.join(invalid)}"}, 400,
            )
            return
        try:
            from src.pipeline.database import get_note_row, update_note_buckets

            updated = _run_db(update_note_buckets(note_id, buckets))
            if not updated:
                self._send_json({"error": "Note not found"}, 404)
                return

            # Keep the Notion page's Bucket property in sync (published notes only).
            row = _run_db(get_note_row(note_id))
            if row and row.get("notion_page_id"):
                from src.integrations.notion_sync import update_page_buckets
                _fire_and_forget(
                    update_page_buckets(row["notion_page_id"], buckets, self._settings),
                    f"Notion bucket sync for {note_id}",
                )

            _regen_notes_cache()
            logger.info("Buckets set for note %s -> %s", note_id, buckets)
            self._send_json({"note_id": note_id, "buckets": buckets})
        except Exception as exc:
            logger.exception("Failed to set buckets for note %s", note_id)
            self._send_json({"error": str(exc)}, 500)

    def _handle_post_review(self, note_id: str, body: dict):
        """Mark a note as reviewed (the "keep" action).

        Request body: {"buckets": [...]} or legacy {"bucket": "..."}.
        Trash is handled by DELETE /api/notes/<id>, not by this endpoint.

        Writes:
            reviewed_at    = now (ISO UTC)
            review_action  = "keep"  (kept for back-compat column)
            buckets        = list[str]
            bucket         = buckets[0]  (legacy single-value mirror)
        """
        from src.config.buckets import is_valid_bucket

        try:
            buckets = self._normalize_buckets_input(body)
        except ValueError as ve:
            self._send_json({"error": str(ve)}, 400)
            return
        if not buckets:
            self._send_json(
                {"error": "At least one bucket is required to keep a note"},
                400,
            )
            return
        invalid = [b for b in buckets if not is_valid_bucket(b)]
        if invalid:
            self._send_json(
                {"error": f"Unknown bucket(s): {', '.join(invalid)}"}, 400,
            )
            return

        try:
            from src.pipeline.database import update_note_review

            now = datetime.now(timezone.utc).isoformat()
            updated = _run_db(update_note_review(note_id, buckets, now))
            if not updated:
                self._send_json({"error": "Note not found"}, 404)
                return

            # Publish-on-keep: kept notes get their Notion page created now
            # (or, if already published, just get buckets synced). Fire-and-
            # forget — the local review state is authoritative regardless.
            if self._settings.enable_notion_sync:
                from src.integrations import publish_note_to_notion
                _fire_and_forget(
                    publish_note_to_notion(note_id, buckets, self._settings),
                    f"Notion publish for kept note {note_id}",
                )

            _regen_notes_cache()
            logger.info("Reviewed note %s — buckets: %s", note_id, buckets)
            self._send_json({
                "reviewed": note_id,
                "buckets": buckets,
                "reviewed_at": now,
            })
        except Exception as exc:
            logger.exception("Failed to review note %s", note_id)
            self._send_json({"error": str(exc)}, 500)

    def _handle_post_edit(self, note_id: str, body: dict):
        """Edit note fields: title, summary, why_keep, tags, key_takeaways.

        Body: any subset of those keys. Local-only — the Notion page body is
        not rewritten (the dashboard is the curation surface).
        """
        try:
            from src.pipeline.database import update_note_fields

            fields = {k: v for k, v in body.items() if v is not None}
            if not fields:
                self._send_json({"error": "No fields to update"}, 400)
                return
            try:
                updated = _run_db(update_note_fields(note_id, fields))
            except ValueError as ve:
                self._send_json({"error": str(ve)}, 400)
                return
            if not updated:
                self._send_json({"error": "Note not found"}, 404)
                return

            _regen_notes_cache()
            logger.info("Edited note %s fields: %s", note_id, sorted(fields))
            self._send_json({"note_id": note_id, "updated": sorted(fields)})
        except Exception as exc:
            logger.exception("Failed to edit note %s", note_id)
            self._send_json({"error": str(exc)}, 500)

    def log_message(self, format, *args):
        """Suppress default access logs — too noisy."""
        pass


def start_api_server(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> Optional[Thread]:
    """Start the API server in a background thread.

    Binds to settings.api_bind_host / settings.api_port by default
    (127.0.0.1 / 8080). Returns the thread, or None if the port is in use.
    """
    settings = get_settings()
    bind_host = host or settings.api_bind_host
    bind_port = port or settings.api_port

    # Create .nojekyll and index redirect if missing
    (_DOCS_DIR / ".nojekyll").touch(exist_ok=True)

    loopback_hosts = ("127.0.0.1", "::1", "localhost")
    if not settings.dashboard_token:
        if bind_host not in loopback_hosts:
            raise RuntimeError(
                "Refusing to start API server: DASHBOARD_TOKEN is not set but "
                f"API_BIND_HOST={bind_host!r} exposes the dashboard to the network. "
                "Set DASHBOARD_TOKEN in your .env file or bind to 127.0.0.1."
            )
        logger.warning(
            "DASHBOARD_TOKEN is not set — unauthenticated local access only."
        )
    if bind_host not in loopback_hosts:
        logger.warning(
            "Dashboard API is bound to %s (network-exposed). "
            "Ensure DASHBOARD_TOKEN is set.",
            bind_host,
        )

    try:
        # ThreadingHTTPServer: a phone holding a keep-alive connection must
        # not serialize every other request. DB access stays safe — all DB
        # coroutines are marshalled onto the single bot event loop.
        server = ThreadingHTTPServer((bind_host, bind_port), DashboardHandler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("Dashboard API server started on http://%s:%d", bind_host, bind_port)
        return thread
    except OSError as exc:
        logger.warning("Could not start API server on %s:%d: %s", bind_host, bind_port, exc)
        return None
