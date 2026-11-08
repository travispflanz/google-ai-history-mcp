import argparse
import asyncio
import json
import sys

from google_ai_history.db import list_chats, search_chats
from google_ai_history.importers import (
    SUPPORTED_TARGETS,
    check_system_health,
    fetch_chat_from_url,
    import_from_path,
    setup_browser_auth,
    sync_from_browser,
)
from google_ai_history.server import mcp


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description="Google AI History MCP Server & CLI")
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Check system prerequisites, dependencies, and Google authentication readiness",
    )
    sub = parser.add_subparsers(dest="cmd")

    fetch_p = sub.add_parser(
        "fetch",
        help="Fetch a single Google AI chat/session directly from a URL",
    )
    fetch_p.add_argument("url", help="Direct or shared URL of the Google AI chat/session")
    fetch_p.add_argument("--timeout", type=int, default=180)

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

    setup_p = sub.add_parser(
        "setup",
        help="Open dedicated Chrome profile and complete one-time Google sign-in",
    )
    setup_p.add_argument("--timeout", type=int, default=180)

    sub.add_parser(
        "doctor",
        aliases=["health"],
        help="Check Python dependencies, Chrome/Edge availability, and authentication readiness",
    )

    sub.add_parser("serve", help="Run the FastMCP stdio server")

    args = parser.parse_args()
    if args.check_auth or args.cmd in ("doctor", "health"):
        res = check_system_health()
        print(json.dumps(res, indent=2))
    elif args.cmd == "setup":
        res = asyncio.run(setup_browser_auth(login_timeout_sec=args.timeout))
        print(json.dumps(res, indent=2))
    elif args.cmd == "fetch":
        res = asyncio.run(fetch_chat_from_url(url=args.url, login_timeout_sec=args.timeout))
        print(json.dumps(res, indent=2))
    elif args.cmd == "sync":
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
