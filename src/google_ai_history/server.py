from typing import Any

from fastmcp import FastMCP

from google_ai_history.db import get_chat, list_chats, search_chats
from google_ai_history.importers import import_from_path, sync_from_browser

mcp = FastMCP("google-ai-history")


@mcp.tool()
def list_google_ai_chats(
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
) -> dict[str, Any]:
    """List locally stored Google AI chats (Gemini, Search AI Mode, AI Studio, Ask Maps, etc.)."""
    return list_chats(limit=limit, offset=offset, source=source)


@mcp.tool()
def search_google_ai_chats(
    query: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Full-text search (SQLite FTS5) across all synced Google AI prompts and responses."""
    return search_chats(query=query, limit=limit)


@mcp.tool()
def get_google_ai_chat(
    chat_id: str,
) -> dict[str, Any]:
    """Retrieve the full conversation transcript and local Markdown path for a chat ID or title."""
    res = get_chat(chat_id=chat_id)
    if not res:
        return {"error": f"No chat found matching '{chat_id}'"}
    return res


@mcp.tool()
def import_google_ai_takeout_or_files(
    path: str,
) -> dict[str, Any]:
    """Import Google AI chats from Takeout, AI Studio Drive folder, NotebookLM, or JSON/MD."""
    return import_from_path(target_path=path)


@mcp.tool()
async def sync_google_ai_chats_from_browser(
    target: str = "gemini",
    max_chats: int = 50,
    login_timeout_sec: int = 180,
) -> dict[str, Any]:
    """Sync Google AI chats via Chrome ('all', 'myactivity', 'gemini', 'aimode', 'aistudio')."""
    return await sync_from_browser(
        max_chats=max_chats,
        login_timeout_sec=login_timeout_sec,
        target=target,
    )
