import sqlite3
from pathlib import Path

from google_ai_history.db import (
    chat_exists_complete,
    delete_superseded_myactivity_stubs,
    get_chat,
    get_conn,
    list_chats,
    search_chats,
    slugify,
    upsert_chat,
)


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


def test_generic_title_guard_legacy_migration_and_skip_complete(tmp_path: Path) -> None:
    upsert_chat(
        chat_id="ais_1",
        title="Fantasy Football Free Agent Claims",
        messages=[{"role": "user", "content": "Order?"}],
        source="browser_aistudio",
        attachments=[{"name": "2025_KC_Keeper_Football_Rules.pdf", "tokens": "6,161 tokens"}],
        base_dir=tmp_path,
    )
    # Generic "Playground" title or "docs" icon name must not overwrite existing good values
    upsert_chat(
        chat_id="ais_1",
        title="Playground",
        messages=[{"role": "user", "content": "Order?"}],
        source="browser_aistudio",
        attachments=[{"name": "docs", "tokens": "6,161 tokens"}],
        base_dir=tmp_path,
    )
    ais_rec = get_chat("ais_1", base_dir=tmp_path)
    assert ais_rec is not None
    assert ais_rec["title"] == "Fantasy Football Free Agent Claims"
    assert ais_rec["attachments"][0]["name"] == "2025_KC_Keeper_Football_Rules.pdf"
    assert chat_exists_complete("ais_1", base_dir=tmp_path) is True
    assert chat_exists_complete("missing_id", base_dir=tmp_path) is False

    # Legacy DB migration (user_version=0): aimode URL canonicalization & stub cleanup
    mig_dir = tmp_path / "mig"
    c0 = get_conn(mig_dir)
    c0.close()
    db_file = mig_dir / "chats.db"
    raw_conn = sqlite3.connect(str(db_file))
    with raw_conn:
        raw_conn.execute("PRAGMA user_version = 0")
        raw_conn.execute(
            "INSERT INTO chats "
            "(chat_id, title, source, url, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "aimode_MTID9",
                "NFL QBs",
                "browser_search_ai_mode",
                "https://www.google.com/search?udm=50&sxsrf=AAA&mtid=MTID9&q=nfl+qbs&ved=BBB",
                "2026-11-11T03:00:00",
                "2026-11-11T03:00:00",
                1,
            ),
        )
        raw_conn.execute(
            "INSERT INTO chats "
            "(chat_id, title, source, url, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "myactivity_gemini_oldstub",
                "Do deep research...",
                "myactivity_gemini",
                "https://myactivity.google.com/product/gemini",
                "2026-11-11T03:00:00",
                "2026-11-11T03:00:00",
                2,
            ),
        )
        raw_conn.execute(
            "INSERT INTO messages (chat_id, turn_index, role, content) VALUES (?, 0, 'user', ?)",
            ("myactivity_gemini_oldstub", "Do deep research on MFL API..."),
        )
        raw_conn.execute(
            "INSERT INTO messages (chat_id, turn_index, role, content) "
            "VALUES (?, 1, 'assistant', ?)",
            ("myactivity_gemini_oldstub", "1:02 PM • Details"),
        )
        raw_conn.execute(
            "INSERT INTO chats "
            "(chat_id, title, source, url, created_at, updated_at, "
            "first_seen_at, source_chat_id, message_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "myactivity_gemini_newfull",
                "Do deep research on MFL API endpoints",
                "myactivity_gemini",
                "https://gemini.google.com/app/0342e975959fbbe1",
                "2026-10-26T17:00:00+00:00",
                "2026-10-26T17:00:00+00:00",
                "2026-11-11T05:00:00+00:00",
                "0342e975959fbbe1",
                2,
            ),
        )
        raw_conn.execute(
            "INSERT INTO messages (chat_id, turn_index, role, content) VALUES (?, 0, 'user', ?)",
            ("myactivity_gemini_newfull", "Do deep research on MFL API endpoints and tools"),
        )
    raw_conn.close()

    aim = get_chat("aimode_MTID9", base_dir=mig_dir)
    assert aim is not None
    assert aim["url"] == "https://www.google.com/search?udm=50&mtid=MTID9&q=nfl+qbs"
    assert aim["created_at"] is None  # bogus sync-time created_at cleared
    assert get_chat("myactivity_gemini_oldstub", base_dir=mig_dir) is None
    assert get_chat("myactivity_gemini_newfull", base_dir=mig_dir) is not None

    # Direct stub deletion helper
    upsert_chat(
        chat_id="myactivity_gemini_stub2",
        title="Truncated prompt...",
        messages=[
            {"role": "user", "content": "Truncated prompt from earlier..."},
            {"role": "assistant", "content": "devices_other\nWindows"},
        ],
        source="myactivity_gemini",
        base_dir=mig_dir,
    )
    removed = delete_superseded_myactivity_stubs(
        source="myactivity_gemini",
        prompt_full="Truncated prompt from earlier with full ending",
        source_chat_id="chat_z",
        keep_chat_id="myactivity_gemini_full2",
        base_dir=mig_dir,
    )
    assert removed == 1
