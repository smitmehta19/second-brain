# Mind Palace — Claude Code Configuration

## Rules

- Do what has been asked; nothing more, nothing less
- NEVER create files unless absolutely necessary — prefer editing existing files
- NEVER create documentation files unless explicitly requested
- NEVER save working files or tests to root — use `/src`, `/tests`, `/docs`, `/config`, `/scripts`
- ALWAYS read a file before editing it
- NEVER commit secrets, credentials, or .env files
- Keep files under 500 lines
- Validate input at system boundaries

## Build & Test

```bash
pip install -e .
python -m src.main
```

## Project Structure

```
src/
├── main.py              # Entry point
├── api/server.py        # Dashboard API
├── bot/                 # Telegram bot
├── categorizer/         # AI categorization
├── config/              # Settings + domains
├── extractors/          # Content extractors
├── integrations/        # Notion + Obsidian sync
├── models/schemas.py    # Data models
├── pipeline/            # DB + processor
├── search/              # Search engine
└── sync/                # Notion→Obsidian sync
```
