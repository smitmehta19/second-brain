# Mind Palace — Project Overview

## Purpose

Mind Palace is a personal knowledge-capture bot that runs on Telegram. The owner sends anything — a URL, a voice note, a photo, plain text, a forwarded message — and the system automatically extracts readable content, categorises it with AI, persists it to SQLite, syncs it to Notion, and optionally mirrors it to a local Obsidian vault. A lightweight HTTP dashboard and a `/ask` Q&A command let the user query their accumulated knowledge. The system is designed to cost $0/month by running exclusively on free-tier AI APIs (Gemini, Groq) and Oracle Cloud Free Tier ARM infrastructure.

---

## High-Level Architecture

```
Telegram message
       │
       ▼
  bot/handlers.py          ← authorisation, input routing
       │
       ▼
  pipeline/processor.py    ← orchestrates all steps, concurrency semaphore
       │
   ┌───┴────────────┬──────────────────┐
   ▼                ▼                  ▼
extractors/      categorizer/       pipeline/
 __init__.py     ai_categorizer.py  database.py
  │               │                  │
  ├─ YouTubeExtractor (yt-dlp)        │
  ├─ WebExtractor                     │
  │   ├─ Jina Reader (primary)        │
  │   ├─ trafilatura / readability    │
  │   └─ Crawl4AI / Playwright        │
  ├─ InstagramExtractor               │
  ├─ SubstackExtractor                └─ aiosqlite → data/secondbrain.db
  ├─ MediaExtractor (image/voice/video)
  └─ TextExtractor                       ▲
                                         │
                                  integrations/
                                   notion_sync.py  → Notion API
                                   obsidian_sync.py (feature-flagged off)
                                         │
                                  sync/notion_to_obsidian.py
                                   (standalone script, run on demand)
                                         │
                                  search/export_json.py
                                   → docs/notes.json
                                         │
                                  api/server.py
                                   → GET /api/notes, /api/stats, /api/credits
                                   → dashboard.html, mindmap.html, para.html
```

---

## Components

### `src/bot/` — Telegram Interface
- **`bot.py`**: registers all handlers, starts `python-telegram-bot` event loop.
- **`handlers.py`**: routes each message type (URL, text, image, voice, document, forward) to `process_capture()`. Implements bot commands: `/ask`, `/search`, `/recent`, `/stats`, `/credits`, `/reprocess`, `/lint`, `/delete`, `/forget`, `/tag`. URL detection handles both `https://` prefixed links and bare domains like `linear.app` (`BARE_DOMAIN_PATTERN`). Strict allow-list: `TELEGRAM_ALLOWED_USERS` must be non-empty or the bot refuses to start (`main.py:38`).
- **`daily_digest.py`**: vault health scoring, daily 8 AM UTC digest, weekly Sunday summary, related-note surfacing.

### `src/pipeline/` — Core Orchestration
- **`processor.py`**: `process_capture()` is the single public entry point for all content. Steps: URL dedup check → `save_capture` → extract → categorise → store → `save_note` → async subprocess to regenerate `notes.json`. Never raises — all errors are caught and returned in `PipelineResult`. Concurrency limited by `asyncio.Semaphore(settings.max_concurrent_jobs)` (`processor.py:56`).
- **`database.py`**: aiosqlite wrapper over a single module-level connection. Schema: `captures`, `notes`, `processing_log`, `api_usage`. Migrations run as `ALTER TABLE ... ADD COLUMN` statements on startup. `notes` has extended columns for `why_keep`, `open_loops`, `structured_data`, `personal_relevance`, `priority`, `action_items` added via migration (`database.py:76–86`).
- **`queue.py`**: background queue for async task scheduling (supplements the semaphore).

### `src/extractors/` — Content Extraction
- **`__init__.py` / `extract_content()`**: dispatcher; detects URL from text if needed, tries platform-specific extractor first, falls back to `WebExtractor`, then returns a minimal fallback. Never raises.
- **`web.py` / `WebExtractor`**: Jina Reader is the primary fetcher (API key rotation, then anonymous). Falls back through trafilatura → readability-lxml → Crawl4AI (Playwright, disabled by `ENABLE_CRAWL4AI=false` for low-RAM hosts). Extracts JSON-LD structured data when it falls back to raw httpx. Calls SerpApi for Amazon URLs and pages without JSON-LD price data.
- **`jina_reader.py`**: per-key quota tracker with daily reset at midnight UTC; rotates across `JINA_API_KEY` + `JINA_API_KEYS` comma-list.
- **`youtube.py`**: uses yt-dlp for metadata + auto-captions transcript.
- **`instagram.py`**, **`substack.py`**: platform-specific scrapers.
- **`media.py`**: saves image/voice/video to vault `_Attachments/`; OCR/transcription deferred to AI categoriser.
- **`url_detector.py`**: strips tracking params (`utm_*`, `fbclid`, etc.); maps domains to `SourcePlatform`; fine-grained `classify_url_content_type()` returns 50+ types (e.g. `ecommerce`, `github_repo`, `arxiv_paper`, `recipe`, `youtube_short`, `long_video`). Second-pass `reclassify_with_content()` catches JS-heavy storefronts by scanning for JSON-LD `Product` schema.
- **`markdown_product_parser.py`**: parses Jina's Markdown for product fields (price, brand, discount) and prepends a structured `=== EXTRACTED PRODUCT DATA ===` block used by the ecommerce keyword fallback.
- **`serpapi_client.py`**: SerpApi integration for product data fallback.

### `src/categorizer/` — AI Categorisation
- **`ai_categorizer.py` / `categorize()`**: LRU cache (1000 entries) keyed on URL or `raw_id`. For URLs: selects a type-specific prompt from 50+ types; for `unknown` URLs with `enable_website_intelligence=True`, uses a 4-phase "Website Intelligence" deep-extraction prompt. For plain text: uses a generic PKM prompt with anti-hallucination MODE A/B routing. Smart pre-skip: if keyword confidence score ≥ 5 hits with no close second domain, AI is bypassed entirely. Falls back to `_keyword_fallback()` → semantic embeddings → keyword counting → always succeeds.
- **`providers.py` / `call_ai()`**: Gemini 2.0 Flash → Groq llama-3.3-70b cascade. Supports N keys per provider via `GEMINI_API_KEYS` / `GROQ_API_KEYS` comma lists. Daily quota exhaustion tracked per key-slot (`gemini_0`, `gemini_1`, ...), resets at midnight UTC. Per-minute rate limits retry with 30/60/60/60s backoff. Raises `AllProvidersExhaustedError` when all keys across both providers are exhausted; caller falls back to keyword categorisation.
- **`prompts.py`**: type-specific system prompts for all 50+ URL content types.

### `src/integrations/` — Storage Output
- **`__init__.py` / `store_content()`**: builds a `StoredNote`, calls `save_to_notion()`. If `reprocess_ctx` contains `old_notion_page_id`, calls `update_notion_page()` instead to update in-place.
- **`notion_sync.py`**: creates/updates Notion pages via `notion-client`. Pure-Python content cleaning pipeline strips HTML, nav phrases, emoji spam, repeated patterns before uploading (`notion_sync.py:64–76`). Chunks blocks at 100 per request; retries rate limits with exponential backoff.
- **`obsidian_sync.py`**: writes YAML-frontmatter Markdown to vault. Feature-flagged off by default (`ENABLE_OBSIDIAN_SYNC=false`).
- **`wiki_meta.py`**: maintains `data/index.json` and `data/log.jsonl` for the wiki/mind-map features.

### `src/sync/` — Notion-to-Obsidian Bridge
- **`notion_to_obsidian.py`**: standalone script (run manually or on a schedule). Pulls pages from Notion databases, writes Obsidian-flavoured Markdown. Maintains `data/sync_state.json` to track last sync timestamp. Supports `--full` and `--since <duration>` flags.

### `src/search/` — Search and Export
- **`engine.py` / `SearchEngine`**: TF-IDF inverted index with domain-taxonomy query expansion. No external deps; operates entirely on the SQLite `notes` table.
- **`embeddings.py`**: optional `sentence-transformers` integration (`pip install mindpalace[semantic]`); provides `match_domains()` for semantic fallback categorisation and `extract_keywords()`.
- **`export_json.py`**: dumps all notes to `docs/notes.json` (consumed by dashboard). Triggered as a subprocess after every new note (`processor.py:231–242`) and by a Docker cron every 6 hours.
- **`vault_qa.py`**: `/ask` command backend. Retrieves relevant notes via `SearchEngine`, assembles a vault context string, and calls `call_ai()` to answer using only stored knowledge.
- **`wiki_lint.py`**: `/lint` command — detects vault drift (missing Notion pages, orphaned files).

### `src/api/` — Dashboard HTTP Server
- **`server.py` / `DashboardHandler`**: `http.server.HTTPServer` running in a background daemon `Thread`. Serves `docs/` static files plus three API endpoints: `GET /api/notes`, `GET /api/stats`, `GET /api/credits`. `DELETE /api/notes/<id>` requires a `Bearer` token (`DASHBOARD_TOKEN`). CORS locked to `DASHBOARD_ORIGIN` (default `127.0.0.1:8080`). Five-page dashboard: `index.html`, `dashboard.html`, `mindmap.html`, `para.html`, `atlas.html`, `concepts.html`.

### `src/config/` — Configuration
- **`settings.py`**: Pydantic `BaseSettings` loaded from `.env`. All multi-key lists expose a computed `all_*_keys` property that merges the primary key with the comma-separated extras.
- **`domains.py`**: hardcoded domain taxonomy with keywords, Obsidian folder mapping, Notion database target, and MOC title per domain. `register_domain()` auto-adds new domains discovered from AI output at runtime.

### `src/utils/`
- **`credit_tracker.py`**: records every AI API call to `api_usage` table; surfaced by `/credits` command and `GET /api/credits`.
- **`helpers.py`**: `now_iso()` and other small utilities.

---

## Data Model

From `src/models/schemas.py`:

| Schema | Role |
|---|---|
| `RawCapture` | Input from Telegram. Fields: `id` (12-char hex UUID), `content_type`, `text`, `url`, `file_path`, `file_id`, `caption`, `sender`, `source_chat`. |
| `ExtractedContent` | Post-extraction. Adds: `title`, `content` (full text), `summary`, `author`, `source_platform`, `url_content_type`, `images`, `metadata`. |
| `CategorizedContent` | Post-AI. Adds: `note_type`, `domains`, `tags`, `folder`, `key_takeaways`, `connections`, `why_keep`, `open_loops`, `structured_data`, `quality_score` (1–5), `personal_relevance` (1–5), `priority`, `action_items`. |
| `StoredNote` | Persisted result. Links `notion_page_id` back to Notion. |
| `PipelineResult` | Returned to bot handler. Contains `StoredNote` or error + `processing_time_ms`. |

`NoteType` values: `fleeting`, `literature`, `evergreen`, `project`, `reference`, `recipe`, `person`.

SQLite tables: `captures`, `notes` (with extended columns from migration), `processing_log`, `api_usage`.

---

## External Integrations

| Integration | Purpose | Free quota |
|---|---|---|
| Telegram Bot API | Input channel; file downloads | Free |
| Jina AI Reader (`r.jina.ai`) | JS-capable web extraction (primary) | 1M tokens/month per key; anonymous fallback |
| Google Gemini 2.0 Flash | AI categorisation (primary) | 15 RPM, 1M tokens/day, 1500 req/day |
| Groq / llama-3.3-70b | AI categorisation (fallback) | 30 RPM, 14.4K req/day |
| Notion API | Primary note storage | Free |
| SerpApi | Product data for Amazon / JSON-LD-less retailers | 100 searches/month per key |
| yt-dlp | YouTube metadata + captions | Free |
| trafilatura / readability-lxml | Static HTML extraction fallback | Free |
| Crawl4AI (Playwright) | JS-rendered fallback (optional, disabled on low-RAM) | Free |
| OpenAI / Claude (Anthropic) | Configured but not the default runtime path | Paid |
| sentence-transformers (optional) | Semantic keyword fallback; domain matching | Free / local |

---

## Configuration

Primary file: `.env` (loaded by `pydantic-settings`). Key variables:

```
# Required
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=123456789   # comma-separated; empty = deny all

# AI (at least one required)
GEMINI_API_KEY=                    # primary
GEMINI_API_KEYS=key2,key3          # rotation pool
GROQ_API_KEY=
GROQ_API_KEYS=key2,key3

# Extraction
JINA_API_KEY=
JINA_API_KEYS=key2,key3
SERPAPI_API_KEY=
SERPAPI_API_KEYS=key2,key3

# Storage
NOTION_API_KEY=
NOTION_INBOX_DATABASE_ID=
NOTION_RESOURCES_DATABASE_ID=
OBSIDIAN_VAULT_PATH=./vault        # only if ENABLE_OBSIDIAN_SYNC=true

# Feature flags
ENABLE_NOTION_SYNC=true
ENABLE_OBSIDIAN_SYNC=false
ENABLE_CRAWL4AI=true               # set false on low-RAM ARM deploy
ENABLE_WEBSITE_INTELLIGENCE=true   # deep prompt for unknown URL types
AI_PROVIDER=auto                   # gemini | groq | auto

# Dashboard
API_PORT=8080
DASHBOARD_TOKEN=                   # required to enable DELETE endpoint
DASHBOARD_ORIGIN=http://127.0.0.1:8080
```

---

## Entry Points and Runtime

**Bot process (always-on):**
```bash
pip install -e .
python -m src.main          # or: mindpalace
```
Startup order (`main.py:startup`):
1. `init_db()` — opens SQLite, runs schema + migrations
2. `initialize_pipeline()` — creates semaphore
3. `init_tracker()` — loads today's API call counts from DB
4. `start_api_server()` — spawns `DashboardHandler` in a daemon thread on `:8080`
5. `run_bot()` — blocks on Telegram long-poll

**Cron jobs (Docker):**
- `0 0 * * *` — `python scripts/generate_mindmap.py` (mind map rebuild)
- `0 */6 * * *` — `python -m src.search.export_json` (notes.json refresh)
- After each note save: `export_json` is also triggered inline as a non-blocking subprocess (`processor.py:231–242`)

**Sync script (on-demand):**
```bash
python -m src.sync.notion_to_obsidian           # incremental
python -m src.sync.notion_to_obsidian --full    # full re-sync
```

**Deployment:** Docker on Oracle Cloud Free Tier ARM (aarch64). `Dockerfile` uses `python:3.12-slim`, installs `ffmpeg` + `git` + `cron`, exposes port 8080. `ENABLE_CRAWL4AI=false` recommended on the 1 GB Free Tier instance.

---

## Notable Design Decisions

**Free-tier-only AI runtime.** The owner uses Claude Max as their development assistant but deliberately routes all bot runtime calls through Gemini and Groq free tiers (zero cost). Claude and OpenAI keys are supported in config but are not the default chain. This is enforced by the provider cascade in `providers.py`: Gemini → Groq → keyword fallback, with no Claude call in the chain unless `AI_PROVIDER=claude` is explicitly set.

**Multi-key rotation.** Every external API (Jina, Gemini, Groq, SerpApi) supports a primary key plus an arbitrary comma-separated extras list. Per-key daily exhaustion is tracked in memory and resets at midnight UTC, allowing multiple free accounts to multiply effective quota.

**Graceful degradation stack.** No step raises to the caller. Extraction: Jina → trafilatura → readability → Crawl4AI → minimal fallback. Categorisation: AI → confident keyword skip → semantic embeddings → keyword counting → ecommerce structured fallback → always returns a `CategorizedContent`. Storage failure logs the error but the pipeline still returns `DONE` with the note ID.

**Type-specific AI prompts.** `url_content_type` drives prompt selection: a GitHub repo, an arXiv paper, an ecommerce product page, a recipe, and a YouTube transcript each receive a different system prompt tuned to extract the right structured fields. The "Website Intelligence" prompt is a 4-phase deep-extraction prompt for uncategorised URLs. The text prompt uses an anti-hallucination MODE A/B switch to handle bare entity names ("fly.io") without inventing facts.

**Obsidian is secondary.** `ENABLE_OBSIDIAN_SYNC` defaults to `false`. Notion is the primary live store; Obsidian is populated by the separate `notion_to_obsidian.py` pull script for local offline access. The vault directory (`./vault`) is still created by Docker for media attachments.

**Dashboard is static + one tiny server.** Rather than a full web framework, the dashboard is five pre-built HTML pages reading a `notes.json` file. The API server is Python's built-in `http.server`, running in a daemon thread inside the bot process. This keeps the deployment to a single process on the ARM VM.
