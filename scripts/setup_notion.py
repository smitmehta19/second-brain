"""Create the Notion databases for Second Brain.

Run once to set up your Notion workspace:
    python scripts/setup_notion.py

Prerequisites:
1. Create a Notion Integration at https://www.notion.so/my-integrations
2. Copy the integration token into your .env as NOTION_API_KEY
3. Create a blank page in Notion where the databases will live
4. Share that page with your integration (click ... → Connections → Add)
5. Copy the page ID from the URL (the 32-char hex string after the workspace name)
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notion_client import Client as NotionClient


def create_databases(notion: NotionClient, parent_page_id: str) -> dict:
    """Create Inbox and Resources databases under the parent page."""

    # Shared properties across both databases
    common_properties = {
        "Title": {"title": {}},
        "Domain": {
            "select": {
                "options": [
                    {"name": "data-engineering", "color": "blue"},
                    {"name": "gen-ai", "color": "purple"},
                    {"name": "data-science", "color": "green"},
                    {"name": "computer-science", "color": "gray"},
                    {"name": "job-search", "color": "yellow"},
                    {"name": "fitness", "color": "orange"},
                    {"name": "cooking", "color": "red"},
                    {"name": "personal-finance", "color": "green"},
                    {"name": "wedding", "color": "pink"},
                    {"name": "politics", "color": "gray"},
                    {"name": "india", "color": "orange"},
                    {"name": "ireland", "color": "green"},
                    {"name": "anime", "color": "purple"},
                    {"name": "market-intelligence", "color": "blue"},
                    {"name": "applied-ai", "color": "purple"},
                ]
            }
        },
        "Tags": {
            "multi_select": {
                "options": [
                    {"name": "type/literature", "color": "blue"},
                    {"name": "type/fleeting", "color": "yellow"},
                    {"name": "type/evergreen", "color": "green"},
                    {"name": "type/recipe", "color": "red"},
                    {"name": "type/reference", "color": "gray"},
                    {"name": "source/youtube", "color": "red"},
                    {"name": "source/instagram", "color": "pink"},
                    {"name": "source/substack", "color": "orange"},
                    {"name": "source/web", "color": "blue"},
                    {"name": "source/thought", "color": "yellow"},
                    {"name": "source/whatsapp", "color": "green"},
                ]
            }
        },
        "Source URL": {"url": {}},
        "Status": {
            "select": {
                "options": [
                    {"name": "Inbox", "color": "yellow"},
                    {"name": "Processed", "color": "green"},
                    {"name": "Archived", "color": "gray"},
                ]
            }
        },
        "Note Type": {
            "select": {
                "options": [
                    {"name": "literature", "color": "blue"},
                    {"name": "fleeting", "color": "yellow"},
                    {"name": "evergreen", "color": "green"},
                    {"name": "recipe", "color": "red"},
                    {"name": "reference", "color": "gray"},
                    {"name": "person", "color": "purple"},
                ]
            }
        },
        "Quality": {"number": {"format": "number"}},
        "Created": {"date": {}},
    }

    # Create Inbox database
    print("Creating Inbox database...")
    inbox = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "Second Brain — Inbox"}}],
        properties=common_properties,
        icon={"type": "emoji", "emoji": "📥"},
    )
    inbox_id = inbox["id"]
    print(f"  Inbox DB ID: {inbox_id}")

    # Create Resources database
    print("Creating Resources database...")
    resources = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "Second Brain — Resources"}}],
        properties=common_properties,
        icon={"type": "emoji", "emoji": "📚"},
    )
    resources_id = resources["id"]
    print(f"  Resources DB ID: {resources_id}")

    return {
        "inbox_id": inbox_id,
        "resources_id": resources_id,
    }


def main():
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        print("ERROR: Set NOTION_API_KEY in your .env file first.")
        print("Get one at: https://www.notion.so/my-integrations")
        sys.exit(1)

    print("=" * 50)
    print("Second Brain — Notion Setup")
    print("=" * 50)
    print()
    print("Steps before running this script:")
    print("1. Create a blank page in Notion")
    print("2. Share it with your integration (... → Connections)")
    print("3. Copy the page ID from the URL")
    print()

    parent_page_id = input("Paste your Notion parent page ID: ").strip()
    if not parent_page_id or len(parent_page_id) < 20:
        print("Invalid page ID. It should be a 32-character hex string from the URL.")
        sys.exit(1)

    # Remove hyphens if present
    parent_page_id = parent_page_id.replace("-", "")

    notion = NotionClient(auth=api_key)

    result = create_databases(notion, parent_page_id)

    # Verify properties were actually written
    print()
    print("Verifying schema...")
    for label, db_id in [("Inbox", result["inbox_id"]), ("Resources", result["resources_id"])]:
        db = notion.databases.retrieve(database_id=db_id)
        props = db.get("properties", {})
        if props:
            print(f"  {label}: {len(props)} properties — OK")
        else:
            print(f"  {label}: WARNING — no properties found.")
            print("    The integration may lack 'Read content' / 'Update content' capabilities.")
            print("    Fix at: https://www.notion.so/my-integrations")
            sys.exit(1)

    print()
    print("=" * 50)
    print("SUCCESS! Add these to your .env file:")
    print("=" * 50)
    print()
    print(f'NOTION_INBOX_DATABASE_ID={result["inbox_id"]}')
    print(f'NOTION_RESOURCES_DATABASE_ID={result["resources_id"]}')
    print()
    print("Then restart your bot.")


if __name__ == "__main__":
    main()
