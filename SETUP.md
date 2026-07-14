# Mind Palace — Complete Setup Guide

> Time: 15 minutes. Cost: $0/month. Fully automated after setup.

## What You're Building

```
You → Telegram bot → AI extracts + categorizes → Notion + Dashboard
                                                       ↓
                              Website: search, filter, delete, mind map
                              Bot: daily digest, search, stats
```

## Prerequisites

- [x] Python 3.10+ installed on your PC
- [ ] Telegram account
- [ ] Google account (for free Gemini AI)
- [ ] Notion account (free)

---

## Option A: Automated Setup (Recommended)

```bash
cd path/to/secondbrain
python scripts/setup.py
```

This walks you through everything interactively. Skip to "Step 6: Deploy" after.

---

## Option B: Manual Setup (Step by Step)

### Step 1: Install Dependencies (2 min)

```bash
cd path/to/secondbrain
pip install -e .
```

### Step 2: Create Telegram Bot (3 min)

1. Open Telegram on your phone
2. Search for **@BotFather** and start a chat
3. Send: `/newbot`
4. Name it: `Smit Brain Bot` (or whatever you want)
5. Username: `smit_brain_bot` (must end in `bot`)
6. **Copy the token** — looks like: `123456:ABC-DEF...`

Now get your user ID:
1. Search for **@userinfobot** on Telegram
2. Send: `/start`
3. **Copy your user ID** — a number like `123456789`

### Step 3: Get Gemini API Key (2 min)

1. Go to: **https://aistudio.google.com/apikey**
2. Sign in with Google
3. Click **"Create API Key"**
4. **Copy the key**

Optional backup: Get a Groq key too at https://console.groq.com/keys

### Step 4: Set Up Notion (5 min)

1. Go to: **https://www.notion.so/my-integrations**
2. Click **"+ New Integration"**
3. Name: `Mind Palace`
4. Select your workspace
5. **Copy the "Internal Integration Secret"**

Now create the databases:
1. Open Notion → create a blank page called **"Mind Palace"**
2. Click `...` (three dots) → **Connections** → Add your `Mind Palace` integration
3. Copy the **page ID** from the URL — it's the 32-character hex string
   - Example URL: `notion.so/My-Page-abc123def456...`
   - Page ID: `abc123def456...`

4. Run:
```bash
python scripts/setup_notion.py
```
5. Paste the page ID when asked
6. **Copy the two database IDs** it outputs

### Step 5: Create .env File (1 min)

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```env
TELEGRAM_BOT_TOKEN=your_token_from_step_2
TELEGRAM_ALLOWED_USERS=your_user_id_from_step_2
GEMINI_API_KEY=your_key_from_step_3
GROQ_API_KEY=your_groq_key_or_leave_empty
NOTION_API_KEY=your_secret_from_step_4
NOTION_INBOX_DATABASE_ID=from_step_4
NOTION_RESOURCES_DATABASE_ID=from_step_4
```

### Step 6: Test Locally (2 min)

```bash
python -m src.main
```

You should see:
```
Pipeline initialised
Dashboard API server started on http://0.0.0.0:8080
Bot is now polling for updates
```

Now test:
1. Open Telegram → find your bot → send `/start`
2. Send a YouTube link: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`
3. Wait 5-10 seconds — bot should respond with title + domain + quality
4. Check Notion — a new page should appear in your Inbox database
5. Open `http://localhost:8080` in browser — dashboard should show the note

**If it works: Stop the bot (Ctrl+C) and move to deployment.**

### Step 7: Deploy to Oracle Cloud — Free Forever (15 min)

#### 7a. Create Oracle Cloud Account

1. Go to **https://cloud.oracle.com**
2. Click "Sign Up" → fill in details
3. You need a credit card, but **you will NOT be charged** (Always Free tier)
4. Select your region (closest to you)
5. Wait for activation (usually instant, sometimes 30 min)

#### 7b. Create the VM

1. Go to **Compute → Instances → Create Instance**
2. Name: `secondbrain`
3. Image: **Ubuntu 22.04**
4. Shape: Click "Change Shape" → **Ampere** tab → **VM.Standard.A1.Flex**
   - OCPUs: `1`
   - Memory: `6 GB`
5. Add SSH key:
   - If you have one: upload your public key
   - If not: let Oracle generate one and download the private key
6. Click **Create**
7. Wait 2 minutes, then copy the **Public IP**

#### 7c. SSH and Deploy

```bash
# From your PC:
ssh ubuntu@YOUR_PUBLIC_IP

# On the server:
sudo apt update && sudo apt install -y git docker.io
sudo systemctl enable docker && sudo systemctl start docker
sudo usermod -aG docker ubuntu

# Clone your code (push to GitHub first, or scp the folder)
git clone https://github.com/YOUR_USERNAME/secondbrain.git
cd secondbrain

# Create .env on server
nano .env
# Paste the same .env content from your PC

# Build and run
sudo docker build -t secondbrain .
sudo docker run -d \
  --name secondbrain \
  --restart unless-stopped \
  --env-file .env \
  -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/docs:/app/docs \
  secondbrain
```

#### 7d. Open Port 8080 for Dashboard

1. In Oracle Cloud Console → Networking → Virtual Cloud Networks
2. Click your VCN → Security Lists → Default
3. Add Ingress Rule:
   - Source: `0.0.0.0/0`
   - Protocol: TCP
   - Port: `8080`
4. Also on the VM:
```bash
sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save
```

#### 7e. Verify

1. Send something to your Telegram bot from your phone
2. Check: `http://YOUR_VM_IP:8080` — your dashboard
3. Check Notion — note should appear

**Done! Your bot runs 24/7 for free. Dashboard at http://YOUR_IP:8080.**

### Step 8: GitHub Pages (Optional — Public Mind Map)

1. Push code to GitHub
2. Go to repo Settings → Pages → Source: "GitHub Actions"
3. The `update-mindmap.yml` workflow deploys daily
4. Your mind map lives at: `https://YOUR_USERNAME.github.io/secondbrain/`

---

## Daily Usage

You do **one thing**: send stuff to your Telegram bot. That's it.

- Forward a WhatsApp message → bot saves it
- Share a YouTube link → bot extracts transcript, summarizes, categorizes
- Share an Instagram reel → bot saves it
- Type a thought → bot files it as a fleeting note
- Send a recipe link → bot detects "cooking", files under Cooking
- Send something about a new topic → bot creates a new category automatically

**Every morning at 8 AM UTC**, the bot sends you a daily digest with:
- Your brain stats
- A random resurfaced note (spaced repetition)

**To search**: `/search machine learning` in Telegram, or use the dashboard.
**To delete**: `/delete <id>` in Telegram, or 🗑️ button on dashboard.

---

## Costs

| Item | Cost |
|------|------|
| Oracle Cloud VM | $0 (Always Free) |
| Telegram Bot API | $0 |
| Gemini AI (1M tokens/day) | $0 |
| Groq AI (backup) | $0 |
| Notion | $0 |
| GitHub Pages | $0 |
| **Total** | **$0/month** |
