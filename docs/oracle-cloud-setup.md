# Oracle Cloud Free Tier — Second Brain Deployment

> Total cost: $0/month forever. Oracle's Always Free tier never expires.

## What You Get (Free Forever)

- **ARM VM**: 4 OCPUs, 24 GB RAM (way more than you need)
- **Boot volume**: 200 GB storage
- **Network**: 10 TB/month outbound

## Step-by-Step Setup (30 minutes)

### 1. Create Oracle Cloud Account

1. Go to [cloud.oracle.com](https://cloud.oracle.com)
2. Sign up — requires a credit card but **you will never be charged** on the free tier
3. Select your home region (pick one closest to you — e.g., UK South London)
4. Wait for account activation (usually instant, sometimes 30 min)

### 2. Create the VM

1. Go to **Compute → Instances → Create Instance**
2. Settings:
   - **Name**: `secondbrain`
   - **Image**: Ubuntu 22.04 (or 24.04)
   - **Shape**: Click "Change Shape" → **Ampere** → **VM.Standard.A1.Flex**
     - OCPUs: **1** (1 is enough, you can use up to 4 free)
     - Memory: **6 GB** (you can use up to 24 free)
   - **Networking**: Accept defaults (creates VCN + public subnet)
   - **SSH Key**: Upload your public key or generate one
     - If you don't have one: run `ssh-keygen -t ed25519` on your PC
3. Click **Create**
4. Wait ~2 minutes for it to provision
5. Copy the **Public IP** from the instance details page

### 3. Open Port 8080 for the Dashboard (VCN ingress rule)

The bot itself only needs outbound traffic (Telegram polling), but the
**dashboard needs an ingress rule** to be reachable from your phone/laptop:

1. Instance details → **Virtual cloud network** link → **Security Lists** → default list
2. **Add Ingress Rule**:
   - Source CIDR: `0.0.0.0/0` (the dashboard is protected by `DASHBOARD_TOKEN`)
   - IP Protocol: TCP, Destination Port: `8080`
3. Save. (The deploy script opens the VM's local iptables port automatically.)

### 4. SSH into Your VM

```bash
ssh -i your_private_key ubuntu@YOUR_PUBLIC_IP
```

On Windows, use PowerShell or Git Bash:
```powershell
ssh -i C:\Users\YOU\.ssh\id_ed25519 ubuntu@YOUR_PUBLIC_IP
```

### 5. Deploy the Bot

```bash
REPO_URL=https://github.com/YOUR_USERNAME/second-brain.git bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/second-brain/main/scripts/deploy_oracle.sh)
# or, after cloning manually:
git clone https://github.com/YOUR_USERNAME/second-brain.git && cd second-brain
REPO_URL=... bash scripts/deploy_oracle.sh
```

The first run creates `.env` from the template and stops so you can fill it in.

### 6. Fill in `.env`

The bot runs on **free-tier Gemini + Groq** (no paid AI keys needed):

```ini
# Telegram (required)
TELEGRAM_BOT_TOKEN=...            # from @BotFather
TELEGRAM_ALLOWED_USERS=[12345678] # your numeric Telegram ID (@userinfobot)

# AI providers — at least one required; both free
GEMINI_API_KEY=...                # aistudio.google.com (primary)
GEMINI_API_KEYS=key2,key3         # optional extra keys for rotation
GROQ_API_KEY=...                  # console.groq.com (fallback)

# Extraction
JINA_API_KEY=...                  # jina.ai — primary URL extractor
ENABLE_CRAWL4AI=false             # skip Playwright/Chromium on ARM

# Notion (curated library — pages are created when you Keep a note in Review)
NOTION_API_KEY=...
NOTION_INBOX_DATABASE_ID=...
NOTION_RESOURCES_DATABASE_ID=...

# Remote dashboard (required for phone access)
API_BIND_HOST=0.0.0.0
DASHBOARD_TOKEN=...               # openssl rand -hex 32 — the server refuses
                                  # to bind publicly without this
DASHBOARD_ORIGIN=http://YOUR_PUBLIC_IP:8080

# Optional
AUTO_ARCHIVE_DAYS=0               # >0 = auto-archive Notion pages untouched N days
ENABLE_OLLAMA_FALLBACK=false      # true if you install Ollama on the VM (tier-3 AI)
```

Then re-run `bash scripts/deploy_oracle.sh`.

### 7. Verify

```bash
curl http://localhost:8080/healthz          # {"status":"ok","notes":N}
sudo docker logs -f mindpalace              # watch startup
```

From your phone/laptop: open `http://YOUR_PUBLIC_IP:8080/` — the dashboard
pages prompt for the token (stored in localStorage).

Send a link to your Telegram bot → it should appear in the dashboard **Review**
queue. Press **Keep** → the Notion page is created (publish-on-Keep).

> **Important:** stop any bot instance running on your PC before starting the
> VM one — two pollers on the same Telegram token conflict.

### Optional: Ollama tier-3 AI fallback

With 6+ GB RAM you can run a local model as a last-resort fallback when both
Gemini and Groq free tiers are exhausted:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2          # ~2 GB, or qwen2.5:7b with 24 GB RAM
```

Set `ENABLE_OLLAMA_FALLBACK=true` and `OLLAMA_URL=http://172.17.0.1:11434`
(the Docker bridge address) in `.env`, then restart the container.

### Updating after a git push

```bash
cd ~/second-brain && git pull
sudo docker stop mindpalace && sudo docker rm mindpalace
sudo docker build -t mindpalace .
sudo docker run -d --name mindpalace --restart unless-stopped --env-file .env \
    -v "$(pwd)/data:/app/data" -p 8080:8080 mindpalace
```

### Where the data lives

- `data/secondbrain.db` — SQLite, the **source of truth** (notes, captures, usage)
- `data/logs/bot.log` — rotating log file
- `data/*.json` — custom buckets, Notion reconcile state
- `docs/notes.json` — regenerated cache only; safe to lose

Back up the `data/` directory; everything else is rebuildable.
