from pathlib import Path

from google_ai_history.db import get_chat, list_chats, search_chats, slugify, upsert_chat


def test_slugify() -> None:
    assert slugify("Hello, World! 123") == "hello_world_123"
    assert slugify("   ***   ") == "untitled"


def test_upsert_list_get_and_search(tmp_path: Path) -> None:
    messages = [
        {
            "role": "user",
            "content": "How do I configure FastMCP with SQLite FTS5?",
            "timestamp": "2026-11-07T10:00:00Z",
        },
        {
            "role": "assistant",
            "content": "You can create a virtual table using FTS5 in SQLite.",
            "timestamp": "2026-11-07T10:00:05Z",
        },
    ]
    res = upsert_chat(
        chat_id="abc123xyz",
        title="FastMCP SQLite FTS5 Guide",
        messages=messages,
        source="browser_gemini",
        url="https://gemini.google.com/app/abc123xyz",
        base_dir=tmp_path,
    )
    assert res["chat_id"] == "abc123xyz"
    assert res["message_count"] == 2
    assert Path(res["markdown_path"]).is_file()

    listed = list_chats(base_dir=tmp_path)
    assert listed["total_chats"] == 1
    assert listed["chats"][0]["title"] == "FastMCP SQLite FTS5 Guide"

    assert list_chats(source="browser_gemini", base_dir=tmp_path)["returned"] == 1
    assert list_chats(source="takeout_gemini", base_dir=tmp_path)["returned"] == 0

    fetched = get_chat("abc123xyz", base_dir=tmp_path)
    assert fetched is not None
    assert len(fetched["messages"]) == 2
    assert fetched["messages"][0]["role"] == "user"

    by_title = get_chat("FTS5 Guide", base_dir=tmp_path)
    assert by_title is not None
    assert by_title["chat_id"] == "abc123xyz"

    assert get_chat("nonexistent_id", base_dir=tmp_path) is None

    search_res = search_chats("virtual table FTS5", base_dir=tmp_path)
    assert search_res["matches"] == 1
    assert search_res["results"][0]["chat_id"] == "abc123xyz"

    empty_res = search_chats("   ", base_dir=tmp_path)
    assert empty_res["matches"] == 0


def test_orphan_markdown_cleanup_timestamps_and_metadata(tmp_path: Path) -> None:
    first = upsert_chat(
        chat_id="0342e975959fbbe1",
        title="MFL Custom API Tools and Scripts",
        messages=[
            {"role": "user", "content": "Do deep research"},
            {
                "role": "assistant",
                "kind": "deep_research_report",
                "content": "Full architectural blueprint",
            },
        ],
        source="browser_gemini",
        url="https://gemini.google.com/app/0342e975959fbbe1",
        incomplete="deep_research_report_not_captured",
        attachments=[
            {
                "name": "rules.pdf",
                "tokens": "6,161 tokens",
                "drive_id": "drive_abc123",
            }
        ],
        base_dir=tmp_path,
    )
    first_md = Path(first["markdown_path"])
    assert first_md.is_file()

    rec1 = get_chat("0342e975959fbbe1", base_dir=tmp_path)
    assert rec1 is not None
    assert rec1["created_at"] is None  # no fake sync timestamp in created_at
    assert rec1["first_seen_at"] is not None
    assert rec1["synced_at"] is not None
    assert rec1["incomplete"] == "deep_research_report_not_captured"
    assert rec1["messages"][1]["kind"] == "deep_research_report"
    assert rec1["attachments"][0]["drive_id"] == "drive_abc123"

    # Re-upsert with a different title and resolved report -> old markdown file must be removed
    second = upsert_chat(
        chat_id="0342e975959fbbe1",
        title="Renamed Custom Tools Blueprint",
        messages=[
            {"role": "user", "content": "Do deep research"},
            {
                "role": "assistant",
                "kind": "deep_research_report",
                "content": "Full architectural blueprint",
                "timestamp": "2026-10-26T17:19:00+00:00",
            },
        ],
        source="browser_gemini",
        created_at="2026-10-26T17:19:00+00:00",
        source_chat_id="0342e975959fbbe1",
        base_dir=tmp_path,
    )
    second_md = Path(second["markdown_path"])
    assert second_md.is_file()
    assert second_md != first_md
    assert not first_md.exists()

    rec2 = get_chat("0342e975959fbbe1", base_dir=tmp_path)
    assert rec2 is not None
    assert rec2["first_seen_at"] == rec1["first_seen_at"]
    assert rec2["created_at"] == "2026-10-26T17:19:00+00:00"
    assert rec2["incomplete"] is None
    assert rec2["source_chat_id"] == "0342e975959fbbe1"
