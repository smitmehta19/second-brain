"""Core data models for the Second Brain pipeline."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class ContentType(str, enum.Enum):
    """Types of content the system can process."""
    URL = "url"
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    DOCUMENT = "document"
    VIDEO = "video"
    CONTACT = "contact"
    LOCATION = "location"
    STICKER = "sticker"
    UNKNOWN = "unknown"


class SourcePlatform(str, enum.Enum):
    """Where the content was originally from."""
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    SUBSTACK = "substack"
    TWITTER = "twitter"
    REDDIT = "reddit"
    GITHUB = "github"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    LINKEDIN = "linkedin"
    MEDIUM = "medium"
    ARXIV = "arxiv"
    WIKIPEDIA = "wikipedia"
    PODCAST = "podcast"
    WEB = "web"
    THOUGHT = "thought"
    UNKNOWN = "unknown"


class NoteType(str, enum.Enum):
    """Obsidian/Notion note classification."""
    FLEETING = "fleeting"
    LITERATURE = "literature"
    EVERGREEN = "evergreen"
    PROJECT = "project"
    REFERENCE = "reference"
    RECIPE = "recipe"
    PERSON = "person"


class ProcessingStatus(str, enum.Enum):
    """Pipeline processing state."""
    QUEUED = "queued"
    EXTRACTING = "extracting"
    CATEGORIZING = "categorizing"
    STORING = "storing"
    DONE = "done"
    FAILED = "failed"


class RawCapture(BaseModel):
    """Raw input from Telegram or any capture source."""
    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:12])
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    content_type: ContentType
    text: Optional[str] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    file_id: Optional[str] = None  # Telegram file ID
    caption: Optional[str] = None
    sender: str = "self"
    source_chat: str = "telegram"


class ExtractedContent(BaseModel):
    """Content after extraction and enrichment."""
    raw_id: str
    title: str
    content: str  # Full extracted text/transcript
    summary: Optional[str] = None
    url: Optional[str] = None
    author: Optional[str] = None
    source_platform: SourcePlatform = SourcePlatform.UNKNOWN
    content_type: ContentType = ContentType.TEXT
    url_content_type: str = "unknown"  # Fine-grained: recipe, github_repo, etc.
    images: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


class CategorizedContent(BaseModel):
    """Content after AI categorization."""
    extracted: ExtractedContent
    note_type: NoteType
    domains: list[str]  # e.g. ["data-engineering", "gen-ai"]
    tags: list[str]  # e.g. ["type/literature", "domain/gen-ai"]
    folder: str  # Target folder in vault
    key_takeaways: list[str] = Field(default_factory=list)
    connections: list[str] = Field(default_factory=list)  # Suggested links
    why_keep: str = ""  # Why this is worth keeping — shown prominently
    open_loops: list[str] = Field(default_factory=list)  # Follow-ups/actions
    structured_data: dict = Field(default_factory=dict)  # Type-specific fields
    quality_score: int = Field(default=3, ge=1, le=5)  # 1-5 rating
    personal_relevance: int = Field(default=3, ge=1, le=5)  # How relevant to user
    priority: str = "medium"  # high/medium/low
    action_items: list[str] = Field(default_factory=list)  # Specific next steps
    bucket: Optional[str] = None  # CAREER|WATCH-LONG|WATCH-SHORT|MAKE|SHOP|READ|INSPIRE
    categorized_at: datetime = Field(default_factory=datetime.utcnow)


class StoredNote(BaseModel):
    """Final stored note with all metadata."""
    id: str
    title: str
    file_path: str  # Path in Obsidian vault
    notion_page_id: Optional[str] = None
    note_type: NoteType
    domains: list[str]
    tags: list[str]
    source_url: Optional[str] = None
    summary: Optional[str] = None
    key_takeaways: list[str] = Field(default_factory=list)
    quality_score: int = 3
    bucket: Optional[str] = None  # CAREER|WATCH-LONG|WATCH-SHORT|MAKE|SHOP|READ|INSPIRE
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: ProcessingStatus = ProcessingStatus.DONE


class PipelineResult(BaseModel):
    """Result of processing a single capture."""
    raw_id: str
    status: ProcessingStatus
    note: Optional[StoredNote] = None
    error: Optional[str] = None
    processing_time_ms: int = 0
