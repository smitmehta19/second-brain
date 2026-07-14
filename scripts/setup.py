"""Interactive setup wizard for Second Brain.

Run: python scripts/setup.py

Walks you through every step:
1. Checks Python version
2. Installs dependencies
3. Asks for API keys
4. Creates .env file
5. Sets up Notion databases
6. Tests the bot connection
7. Generates first notes.json + mind map
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def print_header(text: str):
    print(f"\n{'=' * 55}")
    print(f"  {text}")
    print(f"{'=' * 55}\n")


def print_step(n: int, total: int, text: str):
    print(f"\n  [{n}/{total}] {text}")
    print(f"  {'-' * 40}")


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """Prompt user for input."""
    if default:
        prompt_str = f"  {prompt} [{default}]: "
    else:
        prompt_str = f"  {prompt}: "

    if secret:
        import getpass
        value = getpass.getpass(prompt_str)
    else:
        value = input(prompt_str).strip()

    return value or default


def ask_yn(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    value = input(f"  {prompt} [{d}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


def run(cmd: str, check: bool = True) -> bool:
    try:
        subprocess.run(cmd, shell=True, check=check, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False


def main():
    print_header("MIND PALACE — Setup Wizard")
    print("  This will set up Mind Palace — your AI-powered knowledge engine.")
    print("  Total cost: $0/month. Time: ~15 minutes.\n")
    print("  You'll need:")
    print("    1. A Telegram account")
    print("    2. A Google account (for Gemini API)")
    print("    3. A Notion account (free)")
    print()
    input("  Press Enter to start...")

    total_steps = 7
    env_vars = {}

    # Step 1: Python check
    print_step(1, total_steps, "Checking Python")
    version = sys.version_info
    print(f"  Python {version.major}.{version.minor}.{version.micro}")
    if version < (3, 10):
        print("  ERROR: Python 3.10+ required. Please upgrade.")
        sys.exit(1)
    print("  OK")

    # Step 2: Install dependencies
    print_step(2, total_steps, "Installing dependencies")
    print("  This may take a minute...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  WARNING: Some deps may have failed. Continuing anyway...")
    else:
        print("  All dependencies installed.")

    # Step 3: Telegram Bot
    print_step(3, total_steps, "Telegram Bot Setup")
    print()
    print("  Steps to create your bot:")
    print("    1. Open Telegram → search for @BotFather")
    print("    2. Send: /newbot")
    print("    3. Pick a name (e.g. 'Smit Brain Bot')")
    print("    4. Pick a username (e.g. 'smit_brain_bot')")
    print("    5. Copy the token it gives you")
    print()
    token = ask("Paste your bot token", secret=True)
    if not token:
        print("  ERROR: Bot token is required.")
        sys.exit(1)
    env_vars["TELEGRAM_BOT_TOKEN"] = token

    print()
    print("  Now get your Telegram user ID:")
    print("    1. Open Telegram → search for @userinfobot")
    print("    2. Send: /start")
    print("    3. Copy your user ID (number)")
    print()
    user_id = ask("Your Telegram user ID")
    env_vars["TELEGRAM_ALLOWED_USERS"] = user_id

    # Step 4: AI Provider
    print_step(4, total_steps, "AI Provider (Gemini — Free)")
    print()
    print("  Steps to get Gemini API key:")
    print("    1. Go to: https://aistudio.google.com/apikey")
    print("    2. Sign in with Google")
    print("    3. Click 'Create API Key'")
    print("    4. Copy the key")
    print()
    gemini_key = ask("Paste your Gemini API key", secret=True)
    if gemini_key:
        env_vars["GEMINI_API_KEY"] = gemini_key

    print()
    print("  Optional: Groq as backup (also free)")
    print("    Get key at: https://console.groq.com/keys")
    groq_key = ask("Groq API key (Enter to skip)")
    if groq_key:
        env_vars["GROQ_API_KEY"] = groq_key

    # Step 5: Notion
    print_step(5, total_steps, "Notion Setup")
    print()
    print("  Steps to create Notion integration:")
    print("    1. Go to: https://www.notion.so/my-integrations")
    print("    2. Click '+ New Integration'")
    print("    3. Name it: 'Second Brain'")
    print("    4. Select your workspace")
    print("    5. Copy the 'Internal Integration Secret'")
    print()
    notion_key = ask("Notion integration secret", secret=True)
    if notion_key:
        env_vars["NOTION_API_KEY"] = notion_key

    if notion_key:
        print()
        print("  Now create databases:")
        print("    1. Create a blank page in Notion called 'Second Brain'")
        print("    2. Click ... → Connections → Add your integration")
        print("    3. Copy the page ID from the URL (32-char hex after workspace name)")
        print()
        page_id = ask("Notion page ID (or Enter to do this later)")
        if page_id:
            # Create databases
            try:
                sys.path.insert(0, str(PROJECT_ROOT))
                os.environ["NOTION_API_KEY"] = notion_key
                from scripts.setup_notion import create_databases
                from notion_client import Client
                notion = Client(auth=notion_key)
                result = create_databases(notion, page_id.replace("-", ""))
                env_vars["NOTION_INBOX_DATABASE_ID"] = result["inbox_id"]
                env_vars["NOTION_RESOURCES_DATABASE_ID"] = result["resources_id"]
                print("  Notion databases created!")
            except Exception as exc:
                print(f"  Could not create databases: {exc}")
                print("  You can run 'python scripts/setup_notion.py' later.")

    # Step 6: Write .env
    print_step(6, total_steps, "Creating .env file")
    env_path = PROJECT_ROOT / ".env"

    # Set defaults
    env_vars.setdefault("AI_PROVIDER", "auto")
    env_vars.setdefault("GEMINI_MODEL", "gemini-2.0-flash")
    env_vars.setdefault("GROQ_MODEL", "llama-3.3-70b-versatile")
    env_vars.setdefault("OBSIDIAN_VAULT_PATH", "./vault")
    env_vars.setdefault("MAX_CONCURRENT_JOBS", "5")
    env_vars.setdefault("EXTRACTION_TIMEOUT", "30")
    env_vars.setdefault("AUTO_CATEGORIZE", "true")
    env_vars.setdefault("DB_PATH", "./data/secondbrain.db")
    env_vars.setdefault("ENABLE_NOTION_SYNC", "true")
    env_vars.setdefault("ENABLE_OBSIDIAN_SYNC", "false")
    env_vars.setdefault("ENABLE_VOICE_TRANSCRIPTION", "true")
    env_vars.setdefault("ENABLE_IMAGE_OCR", "true")

    lines = ["# Second Brain — Auto-generated by setup wizard"]
    for key, value in env_vars.items():
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Written to: {env_path}")

    # Step 7: Test
    print_step(7, total_steps, "Testing")
    print("  Testing imports...")
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        os.environ.update(env_vars)
        from src.models.schemas import RawCapture, ContentType
        from src.config.domains import DOMAINS
        print(f"  Models: OK ({len(DOMAINS)} domains)")
    except Exception as exc:
        print(f"  Import error: {exc}")

    # Generate initial mind map + notes.json
    print("  Generating mind map...")
    try:
        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "generate_mindmap.py")],
            capture_output=True, env={**os.environ, **env_vars},
        )
        print("  Mind map: OK")
    except Exception:
        print("  Mind map: skipped (non-critical)")

    # Done
    print_header("MIND PALACE — SETUP COMPLETE!")
    print("  Mind Palace is ready.\n")
    print("  To run locally:")
    print(f"    cd {PROJECT_ROOT}")
    print("    python -m src.main\n")
    print("  Then open Telegram → find your bot → send /start\n")
    print("  To deploy to Oracle Cloud (free, 24/7):")
    print("    See: docs/oracle-cloud-setup.md\n")
    print("  Your dashboard will be at:")
    print("    http://localhost:8080/  (local)")
    print("    http://YOUR_VM_IP:8080/ (after Oracle deploy)\n")
    print(f"  {'=' * 55}")


if __name__ == "__main__":
    main()
