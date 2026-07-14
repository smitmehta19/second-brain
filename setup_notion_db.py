"""One-time setup: configure Notion database properties for Mind Palace.

Run: python setup_notion_db.py

Adds all required properties (Domain, Tags, Status, Priority, etc.)
to the Notion database specified in .env. Safe to re-run — existing
properties are left untouched.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

load_dotenv(override=True)
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DB_ID = os.getenv("NOTION_INBOX_DATABASE_ID")

PROPERTIES_TO_ADD = {
    "Domain": {"select": {"options": [
        {"name": "gen-ai", "color": "purple"},
        {"name": "data-engineering", "color": "blue"},
        {"name": "career", "color": "orange"},
        {"name": "finance", "color": "green"},
        {"name": "cooking", "color": "red"},
        {"name": "fitness", "color": "yellow"},
        {"name": "travel", "color": "pink"},
        {"name": "productivity", "color": "gray"},
    ]}},
    "Tags": {"multi_select": {"options": []}},
    "Status": {"select": {"options": [
        {"name": "Inbox", "color": "default"},
        {"name": "To Review", "color": "yellow"},
        {"name": "Reviewed", "color": "green"},
        {"name": "Archive", "color": "gray"},
        {"name": "Action Required", "color": "red"},
    ]}},
    "Note Type": {"select": {"options": [
        {"name": "literature", "color": "blue"},
        {"name": "fleeting", "color": "gray"},
        {"name": "evergreen", "color": "green"},
        {"name": "reference", "color": "purple"},
        {"name": "recipe", "color": "red"},
        {"name": "person", "color": "orange"},
        {"name": "project", "color": "yellow"},
    ]}},
    "Priority": {"select": {"options": [
        {"name": "High", "color": "red"},
        {"name": "Medium", "color": "yellow"},
        {"name": "Low", "color": "green"},
    ]}},
    "Content Type": {"select": {"options": []}},
    "Quality": {"number": {"format": "number"}},
    "Relevance": {"number": {"format": "number"}},
    "Source URL": {"url": {}},
    "Created": {"date": {}},
}


async def main():
    if not NOTION_API_KEY or not DB_ID:
        print("ERROR: Set NOTION_API_KEY and NOTION_INBOX_DATABASE_ID in .env")
        sys.exit(1)

    try:
        from notion_client import AsyncClient
    except ImportError:
        print("ERROR: pip install notion-client")
        sys.exit(1)

    notion = AsyncClient(auth=NOTION_API_KEY)

    print(f"Connecting to database {DB_ID}...")
    try:
        db = await notion.databases.retrieve(DB_ID)
    except Exception as e:
        print(f"FAILED to access database: {e}")
        print("\nMake sure you've connected the 'Second Brain' integration:")
        print("  1. Open the database page in Notion")
        print("  2. Click ... menu -> Connections -> Connect to -> Second Brain")
        sys.exit(1)

    title = db["title"][0]["plain_text"] if db.get("title") else "Untitled"
    existing = set(db["properties"].keys())
    print(f"Connected: '{title}'")
    print(f"Existing properties: {', '.join(sorted(existing))}")

    to_add = {k: v for k, v in PROPERTIES_TO_ADD.items() if k not in existing}

    if not to_add:
        print("\nAll properties already exist. Nothing to do.")
        return

    print(f"\nAdding {len(to_add)} properties: {', '.join(to_add.keys())}")

    try:
        await notion.databases.update(
            database_id=DB_ID,
            properties=to_add,
        )
        print("SUCCESS: All properties added.")
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

    # Verify
    db = await notion.databases.retrieve(DB_ID)
    final_props = set(db["properties"].keys())
    print(f"\nFinal properties: {', '.join(sorted(final_props))}")

    # Create a test page to verify write access
    print("\nCreating test page...")
    try:
        page = await notion.pages.create(
            parent={"database_id": DB_ID},
            properties={
                "Title": {"title": [{"text": {"content": "Mind Palace Setup Test"}}]},
                "Status": {"select": {"name": "Archive"}},
                "Priority": {"select": {"name": "Low"}},
                "Quality": {"number": 5},
                "Relevance": {"number": 5},
                "Domain": {"select": {"name": "productivity"}},
                "Note Type": {"select": {"name": "reference"}},
            },
            children=[{
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [{"text": {"content": "Mind Palace is connected and working. You can delete this page."}}],
                    "icon": {"type": "emoji", "emoji": "🧠"},
                    "color": "green_background",
                },
            }],
        )
        print(f"SUCCESS: Test page created -> https://notion.so/{page['id'].replace('-', '')}")
        print("\nSetup complete! Your Mind Palace is ready.")
    except Exception as e:
        print(f"Test page creation failed: {e}")
        print("Properties were added but write access may need fixing.")


if __name__ == "__main__":
    asyncio.run(main())
