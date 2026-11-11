from typing import Any

from fastmcp import FastMCP

from google_ai_history.db import get_chat, list_chats, search_chats
from google_ai_history.importers import (
    _normalize_google_ai_url,
    check_system_health,
    fetch_chat_from_url,
    get_sync_job_status,
    import_from_path,
    start_sync_job,
    sync_from_browser,
)

mcp = FastMCP("google-ai-history")


@mcp.tool()
def google_ai_history_auth_status() -> dict[str, Any]:
    """Check Python dependencies, Chrome/Edge path, and whether Google auth cookies exist."""
    return check_system_health()


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
async def get_google_ai_chat(
    chat_id: str,
    login_timeout_sec: int = 45,
) -> dict[str, Any]:
    """Retrieve a full Google AI conversation by chat ID, title, or direct URL."""
    res = get_chat(chat_id=chat_id)
    if res:
        return res
    normalized_url = _normalize_google_ai_url(chat_id)
    if normalized_url.startswith(("http://", "https://")):
        fetched = await fetch_chat_from_url(
            url=normalized_url,
            login_timeout_sec=login_timeout_sec,
        )
        if "chat" in fetched and fetched["chat"]:
            return fetched["chat"]
        return fetched
    return {"error": f"No chat found matching '{chat_id}'"}


@mcp.tool()
async def fetch_google_ai_chat_url(
    url: str,
    login_timeout_sec: int = 45,
) -> dict[str, Any]:
    """Pull a Google AI chat/session directly from a URL to continue or build from it."""
    return await fetch_chat_from_url(url=url, login_timeout_sec=login_timeout_sec)


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
    skip_existing: bool = False,
    offset: int = 0,
    time_budget_sec: int = 140,
    background: bool = False,
) -> dict[str, Any]:
    """Sync Google AI chats via Chrome ('all', 'myactivity', 'gemini', 'aimode', 'aistudio')."""
    if background:
        return start_sync_job(
            max_chats=max_chats,
            login_timeout_sec=login_timeout_sec,
            target=target,
            skip_existing=skip_existing,
            offset=offset,
        )
    return await sync_from_browser(
        max_chats=max_chats,
        login_timeout_sec=login_timeout_sec,
        target=target,
        skip_existing=skip_existing,
        offset=offset,
        time_budget_sec=time_budget_sec,
    )


@mcp.tool()
def get_sync_status(job_id: str | None = None) -> dict[str, Any]:
    """Check progress/results of a background sync job started with background=True."""
    return get_sync_job_status(job_id=job_id)
