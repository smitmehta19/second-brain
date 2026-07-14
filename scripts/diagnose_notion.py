"""Dump the raw Notion database object to understand its structure."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from notion_client import Client as NotionClient

load_dotenv()

notion = NotionClient(auth=os.environ["NOTION_API_KEY"])
inbox_id = os.environ["NOTION_INBOX_DATABASE_ID"]

db = notion.databases.retrieve(database_id=inbox_id)
print(json.dumps(db, indent=2, default=str))
