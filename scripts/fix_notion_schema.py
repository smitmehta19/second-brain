"""Fix Notion database schema — adds any missing properties to both databases.

Run if you see "X is not a property that exists" errors:
    python scripts/fix_notion_schema.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from notion_client import Client as NotionClient

REQUIRED_PROPERTIES = {
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


def fix_database(notion: NotionClient, db_id: str, label: str) -> None:
    print(f"\n{label} ({db_id})")
    print("-" * 60)

    db = notion.databases.retrieve(database_id=db_id)
    properties = db.get("properties") or {}

    if not properties:
        # Integration can see the database but not its schema — print raw object type/keys
        print(f"  WARNING: No properties returned. Object keys: {sorted(db.keys())}")
        print(f"  Object type: {db.get('object')} | archived: {db.get('archived')}")
        print("  Attempting direct schema update anyway...")
    else:
        existing = set(properties.keys())
        print(f"  Existing properties: {sorted(existing)}")

        # Rename title property to 'Title' if needed
        title_prop = next(
            (name for name, prop in properties.items() if prop.get("type") == "title"),
            None,
        )
        if title_prop and title_prop != "Title":
            print(f"  NOTE: Title property is named '{title_prop}' — renaming to 'Title'")
            notion.databases.update(
                database_id=db_id,
                properties={title_prop: {"name": "Title"}},
            )
            print("  Renamed OK")

        missing = {k: v for k, v in REQUIRED_PROPERTIES.items() if k not in existing}
        if not missing:
            print("  All properties present — nothing to fix.")
            return
        print(f"  Missing: {sorted(missing.keys())} — adding...")

    # Add title property + all required properties
    update_props = {"Title": {"title": {}}, **REQUIRED_PROPERTIES}
    try:
        notion.databases.update(database_id=db_id, properties=update_props)
        print("  Fixed OK")
    except Exception as exc:
        print(f"  ERROR: {exc}")
        print()
        print("  This usually means the integration lacks write access to the database.")
        print("  Fix: open the database in Notion → click '...' → Connections → add your integration.")
        raise


def main():
    load_dotenv()

    api_key = os.getenv("NOTION_API_KEY")
    inbox_id = os.getenv("NOTION_INBOX_DATABASE_ID")
    resources_id = os.getenv("NOTION_RESOURCES_DATABASE_ID")

    if not api_key:
        print("ERROR: NOTION_API_KEY not set in .env")
        sys.exit(1)
    if not inbox_id or not resources_id:
        print("ERROR: NOTION_INBOX_DATABASE_ID or NOTION_RESOURCES_DATABASE_ID not set in .env")
        sys.exit(1)

    notion = NotionClient(auth=api_key)

    print("=" * 60)
    print("Mind Palace — Notion Schema Repair")
    print("=" * 60)

    fix_database(notion, inbox_id, "Inbox DB")
    fix_database(notion, resources_id, "Resources DB")

    print("\n" + "=" * 60)
    print("Done. Restart your bot and Notion sync should work.")
    print("=" * 60)


if __name__ == "__main__":
    main()
