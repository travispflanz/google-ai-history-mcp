from typing import Any

from fastmcp import FastMCP

from gemini_web_chats.db import get_chat, list_chats, search_chats
from gemini_web_chats.importers import import_from_path, sync_from_browser

mcp = FastMCP("gemini-web-chats")


@mcp.tool()
def list_gemini_web_chats(
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
) -> dict[str, Any]:
    """List your imported/synced gemini.google.com conversations stored locally on this computer."""
    return list_chats(limit=limit, offset=offset, source=source)


@mcp.tool()
def search_gemini_web_chats(
    query: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Full-text search (SQLite FTS5) across all prompts and responses from gemini.google.com."""
    return search_chats(query=query, limit=limit)


@mcp.tool()
def get_gemini_web_chat(
    chat_id: str,
) -> dict[str, Any]:
    """Retrieve the full conversation transcript and local Markdown path for a chat ID or title."""
    res = get_chat(chat_id=chat_id)
    if not res:
        return {"error": f"No chat found matching '{chat_id}'"}
    return res


@mcp.tool()
def import_gemini_takeout_or_files(
    path: str,
) -> dict[str, Any]:
    """Import Gemini chats from Takeout (.zip, MyActivity.json, MyActivity.html) or JSON/MD dir."""
    return import_from_path(target_path=path)


@mcp.tool()
async def sync_gemini_chats_from_browser(
    max_chats: int = 50,
    login_timeout_sec: int = 180,
) -> dict[str, Any]:
    """Attach to Chrome, scroll your gemini.google.com sidebar, and sync threads locally."""
    return await sync_from_browser(
        max_chats=max_chats,
        login_timeout_sec=login_timeout_sec,
    )
