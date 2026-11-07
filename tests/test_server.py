import json
from pathlib import Path

import pytest

from gemini_web_chats.server import (
    get_gemini_web_chat,
    import_gemini_takeout_or_files,
    list_gemini_web_chats,
    search_gemini_web_chats,
)


def test_mcp_server_tools_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_WEB_CHATS_DIR", str(tmp_path / "data"))

    sample_json = tmp_path / "export.json"
    sample_json.write_text(
        json.dumps(
            {
                "id": "server_test_1",
                "title": "Antigravity MCP Setup",
                "messages": [
                    {
                        "role": "user",
                        "content": "How do I connect gemini.google.com to Antigravity?",
                    },
                    {
                        "role": "assistant",
                        "content": "Install gemini-web-chats-mcp in mcp_config.json.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    imp = import_gemini_takeout_or_files(str(sample_json))
    assert imp["imported_count"] == 1

    listed = list_gemini_web_chats()
    assert listed["total_chats"] == 1

    searched = search_gemini_web_chats("mcp_config.json")
    assert searched["matches"] == 1

    chat = get_gemini_web_chat("server_test_1")
    assert chat["title"] == "Antigravity MCP Setup"
    assert len(chat["messages"]) == 2

    missing = get_gemini_web_chat("does_not_exist")
    assert "error" in missing
