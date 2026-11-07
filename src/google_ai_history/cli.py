import argparse
import asyncio
import json

from google_ai_history.db import list_chats, search_chats
from google_ai_history.importers import SUPPORTED_TARGETS, import_from_path, sync_from_browser
from google_ai_history.server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Google AI History MCP Server & CLI")
    sub = parser.add_subparsers(dest="cmd")

    sync_p = sub.add_parser(
        "sync",
        help="Sync chats from Gemini, Search AI Mode, AI Studio, NotebookLM, or My Activity",
    )
    sync_p.add_argument(
        "--target",
        choices=SUPPORTED_TARGETS,
        default="gemini",
        help="Google AI surface to sync (gemini, aimode, aistudio, notebooklm, myactivity, all)",
    )
    sync_p.add_argument("--max-chats", type=int, default=50)
    sync_p.add_argument("--timeout", type=int, default=180)

    imp_p = sub.add_parser(
        "import",
        help="Import from Google Takeout, Google Drive 'Google AI Studio' folder, or exports",
    )
    imp_p.add_argument("path", help="Path to Takeout zip/json/html or Google AI Studio folder")

    search_p = sub.add_parser("search", help="Search local Google AI chats")
    search_p.add_argument("query", help="Search query")

    list_p = sub.add_parser("list", help="List synced Google AI chats")
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
