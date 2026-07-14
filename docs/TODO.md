# Second Brain — Setup TODO

## Phase 1: Get API Keys (15 min)

- [ ] **Telegram Bot**: Message @BotFather → `/newbot` → copy token
- [ ] **Your Telegram ID**: Message @userinfobot → copy your user ID
- [ ] **Gemini API Key** (FREE): Go to https://aistudio.google.com/apikey → create key
- [ ] **Notion Integration**: Go to https://www.notion.so/my-integrations → create integration → copy token

## Phase 2: Set Up Notion (10 min)

- [ ] Create a blank page in Notion called "Second Brain"
- [ ] Share it with your integration (click `...` → Connections → your integration)
- [ ] Copy the page ID from the URL (32-char hex string)
- [ ] Run: `python scripts/setup_notion.py` → paste page ID → it creates Inbox + Resources databases
- [ ] Copy the database IDs it outputs

## Phase 3: Configure .env (5 min)

- [ ] Copy `.env.example` → `.env`
- [ ] Fill in `TELEGRAM_BOT_TOKEN`
- [ ] Fill in `TELEGRAM_ALLOWED_USERS` (your user ID)
- [ ] Fill in `GEMINI_API_KEY` (free, recommended)
- [ ] Fill in `NOTION_API_KEY`
- [ ] Fill in `NOTION_INBOX_DATABASE_ID`
- [ ] Fill in `NOTION_RESOURCES_DATABASE_ID`
- [ ] Set `ENABLE_OBSIDIAN_SYNC=false` (for cloud deploy; Obsidian syncs locally)

## Phase 4: Test Locally (10 min)

- [ ] Install Python 3.11+
- [ ] Run: `pip install -e .`
- [ ] Run: `python -m src.main`
- [ ] Open Telegram → find your bot → send `/start`
- [ ] Send a YouTube link → verify it appears in Notion
- [ ] Send a random thought → verify it categorizes correctly
- [ ] Send `/stats` → verify it responds
- [ ] Send `/search` with a keyword → verify search works
- [ ] Stop the bot (Ctrl+C)

## Phase 5: Oracle Cloud Deploy (20 min)

- [ ] Sign up at https://cloud.oracle.com (free, needs credit card but won't charge)
- [ ] Wait for account activation
- [ ] Create instance: Ubuntu 22.04, VM.Standard.A1.Flex, 1 OCPU, 6GB RAM
- [ ] Generate SSH key if you don't have one: `ssh-keygen -t ed25519`
- [ ] Copy the public IP from instance details
- [ ] Push your code to GitHub: `git init && git add . && git commit -m "init" && git push`
- [ ] SSH into VM: `ssh -i ~/.ssh/id_ed25519 ubuntu@YOUR_IP`
- [ ] Clone repo: `git clone https://github.com/YOUR_USER/secondbrain.git`
- [ ] Run: `chmod +x scripts/deploy_oracle.sh && ./scripts/deploy_oracle.sh`
- [ ] Edit `.env` on the server: `nano .env`
- [ ] Restart: `sudo docker restart secondbrain`
- [ ] Test: send something to bot from your phone → check Notion

## Phase 6: Obsidian Setup (30 min)

- [ ] Install Obsidian on Windows
- [ ] Create vault named "SecondBrain" at a convenient location
- [ ] Update `OBSIDIAN_VAULT_PATH` in your local `.env` to point to the vault
- [ ] Run Notion→Obsidian sync once: `python -m src.sync.notion_to_obsidian --full`
- [ ] Verify notes appeared in the vault
- [ ] Set up auto-sync: run `scripts/setup_auto_sync.bat` as Administrator
- [ ] Follow `docs/obsidian-second-brain-blueprint.md` for themes + plugins + templates

## Phase 7: Mobile (10 min)

- [ ] Install Obsidian on Android Pixel
- [ ] Purchase Obsidian Sync ($4/mo) at obsidian.md/sync
- [ ] Connect desktop + phone to same remote vault
- [ ] Wait for initial sync
- [ ] Set battery optimization → Unrestricted for Obsidian on Android
- [ ] Install Obsidian on Android tablet, connect sync
- [ ] Test: create note on phone → verify it appears on desktop

## Phase 8: Polish (when you have time)

- [ ] Install AnuPpuccin theme + Style Settings plugin
- [ ] Apply CSS snippets from blueprint
- [ ] Install Iconize plugin, set folder icons
- [ ] Create initial MOCs: Data Engineering, Gen AI, Job Search, Fitness, etc.
- [ ] Create Home dashboard (`_Meta/Home.md`)
- [ ] Set up daily note template + Calendar plugin
- [ ] Try the full daily workflow: morning triage → evening shutdown

## Ongoing

- [ ] Weekly: Sunday evening — 30 min weekly review in Obsidian
- [ ] Monthly: 1st Sunday — 45 min MOC tending
- [ ] If bot goes down: SSH into Oracle VM → `sudo docker restart secondbrain`
- [ ] To update bot: push to GitHub → SSH → `git pull && sudo docker build -t secondbrain . && sudo docker restart secondbrain`

---

## Cost Summary

| Item | Monthly Cost |
|------|-------------|
| Oracle Cloud VM | $0 (always free) |
| Telegram API | $0 |
| Gemini API | $0 (free tier) |
| Notion API | $0 |
| Obsidian Sync | $4 |
| **Total** | **$4/month** |
