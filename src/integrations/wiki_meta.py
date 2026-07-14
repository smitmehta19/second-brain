"""Vault meta-files (``_Meta/index.md`` + ``_Meta/log.md``).

The Mind Palace keeps two append-only meta-files at the root of the Obsidian
vault. They let the LLM (and humans) navigate the wiki without doing full-text
search on every call.

* ``index.md`` — one canonical line per note: ``- [Title](path) | domain | date``.
  Reprocessed notes update their existing line in place (matched by full path).
* ``log.md`` — chronological, append-only: ``## [YYYY-MM-DD] <kind> | Title``.

This module is the single writer for both files. It is process-safe via a
``.lock`` sidecar file (works on Windows + POSIX, no third-party deps).
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_LOCK_RETRY_SECONDS = 0.05
_LOCK_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Cross-process lock (sidecar .lock file)
# ---------------------------------------------------------------------------

@contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    """Best-effort cross-process lock via O_CREAT|O_EXCL sidecar.

    Works on Windows (where ``fcntl`` is unavailable) and POSIX. If the lock
    cannot be acquired within ``_LOCK_TIMEOUT_SECONDS`` we proceed anyway and
    log — a stale lock from a crashed writer should never block forever.
    """
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    acquired = False
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                logger.warning(
                    "Stale lock at %s — proceeding without lock", lock_path
                )
                break
            time.sleep(_LOCK_RETRY_SECONDS)
    try:
        yield
    finally:
        if acquired:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def today_utc() -> str:
    """Return today's date in YYYY-MM-DD format, UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _escape_title(title: str) -> str:
    """Make a title safe for a markdown link label.

    ``[Title](path)`` syntax breaks if the label contains ``]`` or the path
    contains ``)``. Escape both with a backslash. Also collapse newlines.
    """
    if not title:
        return "Untitled"
    return (
        title.replace("\\", "")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("]", "\\]")
        .replace("[", "\\[")
        .strip()
    )


def _normalize_path(relative_path: str) -> str:
    """Use forward slashes in markdown links — portable across Windows/Obsidian."""
    return relative_path.replace("\\", "/")


def _escape_link_path(path: str) -> str:
    """Escape characters that would break the ``(...)`` portion of a md link."""
    return path.replace(")", "\\)").replace("(", "\\(")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_index(
    vault_path: Path,
    title: str,
    relative_path: str,
    domains: list[str] | None = None,
    today: str | None = None,
) -> None:
    """Upsert a single line in ``_Meta/index.md``.

    The line format is::

        - [Title](path) | domain | YYYY-MM-DD

    Dedup is done on the *full normalized path* — substring matches will not
    collide (so ``Foo.md`` will not match ``Foo.md.bak.md``).
    """
    index_path = vault_path / "_Meta" / "index.md"
    norm_path = _normalize_path(relative_path)
    domain_str = (domains[0] if domains else "general") or "general"
    safe_title = _escape_title(title)
    safe_path = _escape_link_path(norm_path)
    today_str = today or today_utc()

    new_line = f"- [{safe_title}]({safe_path}) | {domain_str} | {today_str}\n"
    # Exact suffix used for dedup — full path enclosed in the link delimiters.
    path_anchor = f"]({safe_path}) |"

    with _file_lock(index_path):
        index_path.parent.mkdir(parents=True, exist_ok=True)

        if not index_path.exists():
            index_path.write_text(
                "# Vault Index\n\nAll notes, ordered by ingest date.\n\n" + new_line,
                encoding="utf-8",
            )
            return

        existing = index_path.read_text(encoding="utf-8")
        if path_anchor in existing:
            lines = existing.splitlines(keepends=True)
            lines = [new_line if path_anchor in line else line for line in lines]
            index_path.write_text("".join(lines), encoding="utf-8")
        else:
            with index_path.open("a", encoding="utf-8") as fh:
                fh.write(new_line)


def append_log(
    vault_path: Path,
    title: str,
    today: str | None = None,
    kind: str = "ingest",
) -> None:
    """Append one timestamped entry to ``_Meta/log.md``.

    Format::

        ## [YYYY-MM-DD] <kind> | Title

    Parseable with ``grep "^## \\[" log.md | tail -10``.
    """
    log_path = vault_path / "_Meta" / "log.md"
    safe_title = _escape_title(title)
    today_str = today or today_utc()
    entry = f"\n## [{today_str}] {kind} | {safe_title}\n"

    with _file_lock(log_path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.write_text(
                "# Vault Log\n\nAppend-only record of ingests and queries.\n" + entry,
                encoding="utf-8",
            )
        else:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(entry)
