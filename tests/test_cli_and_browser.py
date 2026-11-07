import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gemini_web_chats.cli import main as cli_main
from gemini_web_chats.importers import find_chrome_executable, sync_from_browser


def test_find_chrome_executable() -> None:
    res = find_chrome_executable()
    assert res is None or isinstance(res, str)


def test_cli_import_search_and_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GEMINI_WEB_CHATS_DIR", str(tmp_path / "data"))
    export_file = tmp_path / "sample.json"
    export_file.write_text(
        json.dumps(
            {
                "id": "cli_test_1",
                "title": "CLI Test Thread",
                "messages": [
                    {"role": "user", "content": "Hello from CLI test"},
                    {"role": "assistant", "content": "Response from CLI test"},
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["gemini-web-chats", "import", str(export_file)])
    cli_main()
    out = json.loads(capsys.readouterr().out)
    assert out["imported_count"] == 1

    monkeypatch.setattr(sys, "argv", ["gemini-web-chats", "list"])
    cli_main()
    listed = json.loads(capsys.readouterr().out)
    assert listed["total_chats"] == 1

    monkeypatch.setattr(sys, "argv", ["gemini-web-chats", "search", "CLI test"])
    cli_main()
    searched = json.loads(capsys.readouterr().out)
    assert searched["matches"] == 1


@pytest.mark.asyncio
async def test_sync_from_browser_all_targets_mocked(tmp_path: Path) -> None:
    mock_page = AsyncMock()
    mock_page.url = "https://gemini.google.com/app"

    mock_page.evaluate = AsyncMock(
        side_effect=[
            # 1. logged_in check -> True
            True,
            # --- Target: gemini ---
            # 2. expand sidebar -> None
            None,
            # 3. collect sidebar items
            [
                {
                    "id": "chat99999999",
                    "url": "https://gemini.google.com/app/chat99999999",
                    "title": "Mocked Sidebar Chat",
                }
            ],
            # 4. scroll conversation container -> None
            None,
            # 5. extract messages
            {
                "pageTitle": "Mocked Sidebar Chat",
                "messages": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello!"},
                ],
            },
            # --- Target: aimode ---
            # 6. open AI mode history drawer
            None,
            # 7. collect AI mode history links
            [
                {
                    "url": "https://www.google.com/search?udm=50&q=rust+async",
                    "title": "Rust Async Traits",
                }
            ],
            # 8. extract AI mode turns
            [{"role": "assistant", "content": "Async functions in traits are stabilized."}],
            # --- Target: aistudio ---
            # 9. collect AI studio library links
            [
                {
                    "id": "prompt_abc_123",
                    "url": "https://aistudio.google.com/prompts/prompt_abc_123",
                    "title": "Prompt Engineering Test",
                }
            ],
            # 10. extract AI studio turns
            [
                {"role": "user", "content": "Test prompt"},
                {"role": "assistant", "content": "Test model output"},
            ],
            # --- Target: notebooklm ---
            # 11. collect NotebookLM links
            [
                {
                    "id": "nb_xyz_789",
                    "url": "https://notebooklm.google.com/notebook/nb_xyz_789",
                    "title": "AI Architecture Notebook",
                }
            ],
            # 12. extract NotebookLM content
            "Summary of AI architecture sources and notes.",
            # --- Target: myactivity ---
            # 13. myactivity gemini cards
            [
                {
                    "title": "Prompted How does Google My Activity centralize AI logs?",
                    "body": "It stores Gemini, Search AI Mode, Maps, and Assistant logs.",
                }
            ],
            # 14. myactivity search_ai cards
            [
                {
                    "title": "Asked in AI Mode Best local vector database",
                    "body": "SQLite FTS5 and sqlite-vec are great local options.",
                }
            ],
        ]
    )

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    mock_browser = MagicMock()
    mock_browser.contexts = [mock_context]

    mock_pw_instance = AsyncMock()
    mock_pw_instance.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_instance)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("playwright.async_api.async_playwright", return_value=mock_pw_cm):
        res = await sync_from_browser(
            max_chats=1,
            login_timeout_sec=5,
            target="all",
            base_dir=tmp_path,
        )

    assert res["status"] == "ok"
    assert res["synced_count"] == 6

    bad = await sync_from_browser(target="invalid_target", base_dir=tmp_path)
    assert "error" in bad
