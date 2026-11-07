import json
from pathlib import Path

import pytest

from google_ai_history.server import (
    get_google_ai_chat,
    import_google_ai_takeout_or_files,
    list_google_ai_chats,
    search_google_ai_chats,
)


def test_mcp_server_tools_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_AI_HISTORY_DIR", str(tmp_path / "data"))

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

    chat = get_google_ai_chat("server_test_1")
    assert chat["title"] == "Antigravity MCP Setup"
    assert len(chat["messages"]) == 2

    missing = get_google_ai_chat("does_not_exist")
    assert "error" in missing
