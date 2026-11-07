import argparse
import asyncio
import json

from gemini_web_chats.db import list_chats, search_chats
from gemini_web_chats.importers import SUPPORTED_TARGETS, import_from_path, sync_from_browser
from gemini_web_chats.server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini & Google AI Web Chats MCP Server & CLI")
    sub = parser.add_subparsers(dest="cmd")

    sync_p = sub.add_parser(
        "sync",
        help="Sync chats from Gemini, Google Search AI Mode, or AI Studio via Chrome",
    )
    sync_p.add_argument(
        "--target",
        choices=SUPPORTED_TARGETS,
        default="gemini",
        help="Which Google AI surface to sync (gemini, aimode, aistudio, all)",
    )
    sync_p.add_argument("--max-chats", type=int, default=50)
    sync_p.add_argument("--timeout", type=int, default=180)

    imp_p = sub.add_parser(
        "import",
        help="Import from Google Takeout, Google Drive 'Google AI Studio' folder, or exports",
    )
    imp_p.add_argument("path", help="Path to Takeout zip/json/html or Google AI Studio folder")

    search_p = sub.add_parser("search", help="Search local Google AI web chats")
    search_p.add_argument("query", help="Search query")

    list_p = sub.add_parser("list", help="List synced Google AI web chats")
    list_p.add_argument("--source", default=None, help="Filter by source tag")

    sub.add_parser("serve", help="Run the FastMCP stdio server")

    args = parser.parse_args()
    if args.cmd == "sync":
        res = asyncio.run(
            sync_from_browser(
                max_chats=args.max_chats,
                login_timeout_sec=args.timeout,
                target=args.target,
            )
        )
        print(json.dumps(res, indent=2))
    elif args.cmd == "import":
        res = import_from_path(args.path)
        print(json.dumps(res, indent=2))
    elif args.cmd == "search":
        res = search_chats(args.query)
        print(json.dumps(res, indent=2))
    elif args.cmd == "list":
        res = list_chats(source=args.source)
        print(json.dumps(res, indent=2))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
