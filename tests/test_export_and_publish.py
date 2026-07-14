"""Smoke tests: multi-bucket export round-trip and publish-on-keep gating."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.schemas import (
    CategorizedContent,
    ContentType,
    ExtractedContent,
    NoteType,
    StoredNote,
)


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    """Isolated DB: init a fresh SQLite file and reset the module connection."""
    import src.pipeline.database as db

    monkeypatch.setattr(db, "_db", None)
    await db.init_db(str(tmp_path / "test.db"))
    yield db
    await db.close_db()


def _stored_note(note_id: str = "abc123") -> StoredNote:
    return StoredNote(
        id=note_id, title="Test note", file_path="",
        note_type=NoteType.LITERATURE, domains=["gen-ai"],
        tags=["t"], source_url="https://example.com/a",
    )


def _categorized() -> CategorizedContent:
    return CategorizedContent(
        extracted=ExtractedContent(
            raw_id="cap1", title="Test note", content="hello world",
            url="https://example.com/a", content_type=ContentType.URL,
        ),
        note_type=NoteType.LITERATURE, domains=["gen-ai"], tags=["t"],
        folder="00-Inbox", bucket="READ",
    )


async def test_multibucket_survives_export_roundtrip(temp_db, tmp_path):
    """A note assigned to 2 buckets must keep both in the JSON export."""
    from src.search.export_json import export_from_db

    db = temp_db
    await db.save_note(_stored_note(), "cap1", bucket="READ")
    assert await db.update_note_buckets("abc123", ["READ", "CAREER"])

    out = tmp_path / "notes.json"
    count = await export_from_db(str(tmp_path / "test.db"), out)
    assert count == 1
    exported = json.loads(out.read_text(encoding="utf-8"))[0]
    assert exported["buckets"] == ["READ", "CAREER"]
    assert exported["bucket"] == "READ"  # legacy mirror


async def test_dashboard_fetch_shapes_buckets(temp_db):
    db = temp_db
    await db.save_note(_stored_note(), "cap1", bucket="READ")
    notes = await db.fetch_dashboard_notes()
    assert len(notes) == 1
    assert notes[0]["buckets"] == ["READ"]


async def test_review_update_and_queue(temp_db):
    db = temp_db
    await db.save_note(_stored_note(), "cap1", bucket="READ")

    queue = await db.fetch_review_queue(days=7)
    assert [n["id"] for n in queue] == ["abc123"]

    assert await db.update_note_review("abc123", ["READ", "MAKE"], "2026-07-14T00:00:00+00:00")
    queue = await db.fetch_review_queue(days=7)
    assert queue == []

    row = await db.get_note_row("abc123")
    assert json.loads(row["buckets"]) == ["READ", "MAKE"]
    assert row["review_action"] == "keep"


async def test_store_content_defers_notion_when_publish_on_keep(temp_db, monkeypatch):
    """With publish_on_keep on, store_content must NOT call save_to_notion."""
    import src.integrations as integrations

    called = {"notion": False}

    async def fake_save_to_notion(content, settings):
        called["notion"] = True
        return "page-id"

    monkeypatch.setattr(integrations, "save_to_notion", fake_save_to_notion)

    class FakeSettings:
        publish_on_keep = True
        enable_notion_sync = True
        notion_api_key = "x"

    stored = await integrations.store_content(_categorized(), FakeSettings())
    assert stored.notion_page_id is None
    assert called["notion"] is False


async def test_content_snapshot_roundtrip(temp_db):
    """content_json must rebuild into an identical CategorizedContent."""
    db = temp_db
    snapshot = _categorized().model_dump_json()
    await db.save_note(_stored_note(), "cap1", bucket="READ", content_json=snapshot)

    row = await db.get_note_row("abc123")
    rebuilt = CategorizedContent.model_validate_json(row["content_json"])
    assert rebuilt.extracted.title == "Test note"
    assert rebuilt.bucket == "READ"
