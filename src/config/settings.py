"""Configuration management for Second Brain."""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Telegram
    telegram_bot_token: str = Field(..., description="Telegram Bot API token")
    telegram_allowed_users: list[int] = Field(
        default_factory=list,
        description="Telegram user IDs allowed to use the bot. Empty list = deny everyone (secure default).",
    )

    # Dashboard API
    api_bind_host: str = Field(
        default="127.0.0.1",
        description="Bind host for the dashboard API. Use 127.0.0.1 for local-only (default), 0.0.0.0 only if you intentionally want LAN access.",
    )
    api_port: int = Field(default=8080, description="Port for the dashboard API server")
    dashboard_token: Optional[str] = Field(
        default=None,
        description="Bearer token required for destructive API operations (DELETE). If unset, all destructive endpoints are disabled.",
    )
    dashboard_origin: str = Field(
        default="http://127.0.0.1:8080",
        description="Allowed CORS origin for the dashboard (exact match, no wildcards).",
    )

    # AI Providers (at least one required — all have free tiers except Claude/OpenAI)
    ai_provider: str = Field(
        default="auto",
        description="Preferred AI provider: auto, gemini, groq, ollama, openai, claude",
    )

    # Gemini (FREE: 15 RPM, 1M tokens/day) — recommended
    gemini_api_key: Optional[str] = Field(default=None, description="Google Gemini API key")
    gemini_model: str = Field(default="gemini-2.0-flash", description="Gemini model")
    # Extra Gemini keys for rotation — add more free accounts: GEMINI_API_KEYS=key2,key3
    gemini_api_keys: str = Field(default="", description="Additional Gemini API keys for rotation (comma-separated)")

    # Groq (FREE: 30 RPM, 14.4K req/day; 12K TPM on free tier)
    groq_api_key: Optional[str] = Field(default=None, description="Groq API key")
    groq_model: str = Field(default="llama-3.3-70b-versatile", description="Groq model")
    # Extra Groq keys for rotation — add more free accounts: GROQ_API_KEYS=key2,key3
    groq_api_keys: str = Field(default="", description="Additional Groq API keys for rotation (comma-separated)")

    # Ollama (FREE: local, no API key)
    ollama_url: str = Field(default="http://localhost:11434", description="Ollama base URL")
    ollama_model: str = Field(default="llama3.2", description="Ollama model name")

    # OpenAI (paid, cheap with gpt-4o-mini)
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API key")
    openai_model: str = Field(default="gpt-4o-mini", description="OpenAI model")

    # Jina AI Reader — PRIMARY extractor (FREE: 1M tokens/month per key, ~200 RPM anonymous fallback)
    jina_api_key: Optional[str] = Field(default=None, description="Jina AI Reader primary key (jina.ai, 1M tokens/month free)")
    jina_api_keys: str = Field(default="", description="Extra Jina keys for rotation (JINA_API_KEYS=key2,key3)")

    # SerpApi (FREE: 100 searches/month per account) — product data fallback for Amazon + sites without JSON-LD
    serpapi_api_key: Optional[str] = Field(default=None, description="SerpApi key (serpapi.com, 100 free/month)")
    serpapi_api_keys: str = Field(default="", description="Additional SerpApi keys for rotation (comma-separated: SERPAPI_API_KEYS=key2,key3)")

    # Claude (paid, highest quality)
    anthropic_api_key: Optional[str] = Field(default=None, description="Anthropic API key")
    claude_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Claude model for categorization",
    )

    # Notion
    notion_api_key: Optional[str] = Field(default=None, description="Notion integration token")
    notion_inbox_database_id: Optional[str] = Field(
        default=None, description="Notion database ID for inbox"
    )
    notion_resources_database_id: Optional[str] = Field(
        default=None, description="Notion database ID for resources"
    )
    notion_reconcile_interval_min: int = Field(
        default=5,
        description=(
            "How often (minutes) to poll Notion for newly-archived pages and "
            "hard-delete the matching local notes. Set to 0 or negative to disable."
        ),
    )

    # Obsidian Vault
    obsidian_vault_path: str = Field(
        default="./vault",
        description="Path to Obsidian vault root",
    )

    # Pipeline
    max_concurrent_jobs: int = Field(default=5, description="Max parallel processing jobs")
    extraction_timeout: int = Field(default=30, description="Timeout for content extraction (seconds)")
    auto_categorize: bool = Field(default=True, description="Auto-categorize on capture")

    # Database
    db_path: str = Field(default="./data/secondbrain.db", description="SQLite database path")

    # Feature flags
    enable_notion_sync: bool = Field(default=True, description="Sync to Notion")
    enable_obsidian_sync: bool = Field(default=False, description="Sync to Obsidian vault (off by default — use Notion only)")
    enable_voice_transcription: bool = Field(default=True, description="Transcribe voice messages")
    enable_image_ocr: bool = Field(default=True, description="OCR images and screenshots")
    enable_website_intelligence: bool = Field(
        default=True,
        description="For URLs whose classifier returns 'unknown', use the deep 4-phase Website Intelligence prompt instead of the shallow fallback. Set false to roll back without code changes.",
    )
    enable_jina: bool = Field(default=True, description="Use Jina AI Reader as JS-rendering fallback when page content is thin")
    enable_crawl4ai: bool = Field(default=True, description="Use Crawl4AI (local Playwright) as last-resort JS fallback. Disable on low-RAM deploys (ENABLE_CRAWL4AI=false)")

    # Go-live features (2026-07)
    publish_on_keep: bool = Field(
        default=True,
        description=(
            "Create the Notion page only when a note is Review-Kept on the dashboard, "
            "instead of on capture. Trashed/DUMP notes never reach Notion. "
            "Set false to restore publish-on-capture."
        ),
    )
    auto_archive_days: int = Field(
        default=0,
        description="Archive Notion pages for notes untouched this many days (0 = disabled).",
    )
    enable_ollama_fallback: bool = Field(
        default=False,
        description="Use local Ollama as tier-3 AI fallback after Gemini and Groq are exhausted.",
    )
    enable_embeddings: bool = Field(
        default=False,
        description="Use Gemini embeddings (free tier, separate quota) for related-notes and duplicate detection.",
    )
    gemini_embedding_model: str = Field(
        default="text-embedding-004", description="Gemini embedding model"
    )
    extraction_confidence_threshold: float = Field(
        default=0.5,
        description="Extractions scoring below this (0-1) are flagged in the Review queue for re-extraction.",
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def all_jina_keys(self) -> list[str]:
        keys = [self.jina_api_key] if self.jina_api_key else []
        for k in self.jina_api_keys.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    @property
    def all_serpapi_keys(self) -> list[str]:
        keys = [self.serpapi_api_key] if self.serpapi_api_key else []
        for k in self.serpapi_api_keys.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    @property
    def all_gemini_keys(self) -> list[str]:
        keys = [self.gemini_api_key] if self.gemini_api_key else []
        for k in self.gemini_api_keys.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    @property
    def all_groq_keys(self) -> list[str]:
        keys = [self.groq_api_key] if self.groq_api_key else []
        for k in self.groq_api_keys.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    @property
    def vault_path(self) -> Path:
        return Path(self.obsidian_vault_path).resolve()

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).parent


@functools.lru_cache(maxsize=None)
def get_settings() -> Settings:
    """Load settings from environment (cached — reads .env once per process)."""
    return Settings()
