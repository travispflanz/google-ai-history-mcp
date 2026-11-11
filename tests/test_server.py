import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from google_ai_history.server import (
    fetch_google_ai_chat_url,
    get_google_ai_chat,
    get_sync_status,
    google_ai_history_auth_status,
    import_google_ai_takeout_or_files,
    list_google_ai_chats,
    search_google_ai_chats,
    sync_google_ai_chats_from_browser,
)


@pytest.mark.asyncio
async def test_mcp_server_tools_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_AI_HISTORY_DIR", str(tmp_path / "data"))
    auth_stat = google_ai_history_auth_status()
    assert auth_stat["public_share_links_ready"] is True
    assert auth_stat["authenticated_surfaces_ready"] is False

    sample_json = tmp_path / "export.json"
    sample_json.write_text(
        json.dumps(
            {
                "id": "server_test_1",
                "title": "Antigravity MCP Setup",
                "messages": [
                    {"role": "user", "content": "How do I connect Google AI to Antigravity?"},
                    {
                        "role": "assistant",
                        "content": "Install google-ai-history-mcp in mcp_config.json.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    imp = import_google_ai_takeout_or_files(str(sample_json))
    assert imp["imported_count"] == 1

    listed = list_google_ai_chats()
    assert listed["total_chats"] == 1

    searched = search_google_ai_chats("mcp_config.json")
    assert searched["matches"] == 1

    chat = await get_google_ai_chat("server_test_1")
    assert chat["title"] == "Antigravity MCP Setup"
    assert len(chat["messages"]) == 2

    missing = await get_google_ai_chat("does_not_exist")
    assert "error" in missing

    # Test live URL fallback in get_google_ai_chat & fetch_google_ai_chat_url
    mock_fetched = {
        "status": "ok",
        "chat": {
            "chat_id": "abc_url_chat",
            "title": "Continued Project from URL",
            "messages": [{"role": "user", "content": "Build an API"}],
        },
    }
    with patch(
        "google_ai_history.server.fetch_chat_from_url",
        new=AsyncMock(return_value=mock_fetched),
    ):
        from_url = await get_google_ai_chat("https://gemini.google.com/app/abc_url_chat")
        assert from_url["title"] == "Continued Project from URL"

        direct_fetch = await fetch_google_ai_chat_url("https://gemini.google.com/app/abc_url_chat")
        assert direct_fetch["status"] == "ok"

    # Test background sync job + get_sync_status
    import asyncio

    with patch(
        "google_ai_history.importers.sync_from_browser",
        new=AsyncMock(return_value={"status": "ok", "synced_count": 2}),
    ):
        started = await sync_google_ai_chats_from_browser(
            target="gemini", max_chats=10, background=True
        )
        assert started["status"] == "started"
        job_id = started["job_id"]
        await asyncio.sleep(0.05)
        status = get_sync_status(job_id)
        assert status["status"] == "completed"
        assert status["result"]["synced_count"] == 2
        all_jobs = get_sync_status()
        assert any(j["job_id"] == job_id for j in all_jobs["jobs"])
        assert "error" in get_sync_status("unknown_job")
