import argparse
import asyncio
import json

from gemini_web_chats.db import list_chats, search_chats
from gemini_web_chats.importers import import_from_path, sync_from_browser
from gemini_web_chats.server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Web Chats MCP Server & CLI")
    sub = parser.add_subparsers(dest="cmd")

    sync_p = sub.add_parser("sync", help="Sync chats from gemini.google.com via Chrome")
    sync_p.add_argument("--max-chats", type=int, default=50)
    sync_p.add_argument("--timeout", type=int, default=180)

    imp_p = sub.add_parser(
        "import",
        help="Import from Google Takeout (.zip/.json/.html) or folder",
    )
    imp_p.add_argument("path", help="Path to Takeout zip/json/html or exported folder")

    search_p = sub.add_parser("search", help="Search local Gemini web chats")
    search_p.add_argument("query", help="Search query")

    sub.add_parser("list", help="List synced Gemini web chats")
    sub.add_parser("serve", help="Run the FastMCP stdio server")

    args = parser.parse_args()
    if args.cmd == "sync":
        res = asyncio.run(
            sync_from_browser(max_chats=args.max_chats, login_timeout_sec=args.timeout)
        )
        print(json.dumps(res, indent=2))
    elif args.cmd == "import":
        res = import_from_path(args.path)
        print(json.dumps(res, indent=2))
    elif args.cmd == "search":
        res = search_chats(args.query)
        print(json.dumps(res, indent=2))
    elif args.cmd == "list":
        res = list_chats()
        print(json.dumps(res, indent=2))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
