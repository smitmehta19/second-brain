#!/bin/bash
# ============================================================
#  Deploy Mind Palace to Oracle Cloud Free Tier
#  Run this on your Oracle Cloud VM after SSH-ing in
# ============================================================

set -e

echo "=== Mind Palace — Oracle Cloud Deployment ==="
echo ""

# 1. Install Docker
echo "[1/5] Installing Docker..."
sudo apt-get update -qq
sudo apt-get install -y -qq docker.io
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker "$USER"
echo "Docker installed."

# 2. Clone the repo
echo ""
echo "[2/5] Cloning repository..."
if [ -z "$REPO_URL" ]; then
    echo "Set REPO_URL first, e.g.:"
    echo "  REPO_URL=https://github.com/<you>/second-brain.git bash scripts/deploy_oracle.sh"
    exit 1
fi
REPO_DIR="second-brain"
if [ ! -d "$REPO_DIR" ]; then
    git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

# 3. Create .env from example if it doesn't exist yet
echo ""
echo "[3/5] Setting up environment..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "┌──────────────────────────────────────────────┐"
    echo "│         FILL IN YOUR .env FILE NOW           │"
    echo "└──────────────────────────────────────────────┘"
    echo ""
    echo "Open it with:  nano .env"
    echo ""
    echo "Required keys:"
    echo "  TELEGRAM_BOT_TOKEN=          # from @BotFather"
    echo "  TELEGRAM_ALLOWED_USERS=[ID]  # your numeric Telegram ID"
    echo "  GEMINI_API_KEY=              # free at aistudio.google.com"
    echo "  GROQ_API_KEY=                # free at console.groq.com (fallback)"
    echo "  NOTION_API_KEY=              # from notion.so/my-integrations"
    echo "  NOTION_INBOX_DATABASE_ID=    # from setup_notion.py output"
    echo "  NOTION_RESOURCES_DATABASE_ID="
    echo ""
    echo "Required for the remote dashboard:"
    echo "  API_BIND_HOST=0.0.0.0        # expose inside the container"
    echo "  DASHBOARD_TOKEN=             # long random string (openssl rand -hex 32)"
    echo "  DASHBOARD_ORIGIN=http://<VM_PUBLIC_IP>:8080"
    echo ""
    echo "Recommended on ARM free tier:"
    echo "  ENABLE_CRAWL4AI=false        # skip Playwright/Chromium"
    echo ""
    echo "After editing, re-run:  bash scripts/deploy_oracle.sh"
    exit 0
fi

# Safety: the server refuses non-loopback bind without a token; catch it early.
if grep -q "^API_BIND_HOST=0.0.0.0" .env && ! grep -q "^DASHBOARD_TOKEN=." .env; then
    echo "ERROR: API_BIND_HOST=0.0.0.0 requires DASHBOARD_TOKEN to be set in .env"
    exit 1
fi

# 4. Open port 8080 in the VM's local firewall (Oracle Ubuntu images ship
#    restrictive iptables rules — the VCN security-list rule alone is not enough)
echo ""
echo "[4/6] Opening port 8080 in the local firewall..."
if ! sudo iptables -C INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT
    sudo apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
    sudo netfilter-persistent save 2>/dev/null || true
fi
echo "Port 8080 open locally (remember the VCN security-list ingress rule too)."

# 5. Remove any existing container, then build fresh
echo ""
echo "[5/6] Building Docker image (this takes ~2 min first time)..."
sudo docker stop mindpalace 2>/dev/null || true
sudo docker rm   mindpalace 2>/dev/null || true
sudo docker build -t mindpalace .

# 6. Start with restart policy so it survives reboots
echo ""
echo "[6/6] Starting container..."
sudo docker run -d \
    --name mindpalace \
    --restart unless-stopped \
    --env-file .env \
    -v "$(pwd)/data:/app/data" \
    -p 8080:8080 \
    mindpalace

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║         DEPLOYMENT COMPLETE                  ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Check bot logs:"
echo "  sudo docker logs -f mindpalace"
echo ""
echo "Health check:"
echo "  curl http://$(curl -s ifconfig.me):8080/healthz"
echo ""
echo "Dashboard (needs the VCN ingress rule for 8080 + your DASHBOARD_TOKEN):"
echo "  http://$(curl -s ifconfig.me):8080/"
echo ""
echo "Useful commands:"
echo "  sudo docker restart mindpalace              # restart bot"
echo "  sudo docker logs --tail 50 mindpalace       # recent logs"
echo "  sudo docker exec -it mindpalace bash        # shell into container"
echo ""
echo "To update after a git push:"
echo "  cd ~/mindpalace && git pull"
echo "  sudo docker stop mindpalace && sudo docker rm mindpalace"
echo "  sudo docker build -t mindpalace ."
echo "  sudo docker run -d --name mindpalace --restart unless-stopped --env-file .env -v \$(pwd)/data:/app/data -p 8080:8080 mindpalace"
