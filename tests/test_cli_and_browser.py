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
async def test_sync_from_browser_mocked(tmp_path: Path) -> None:
    mock_page = AsyncMock()
    mock_page.url = "https://gemini.google.com/app"

    # Sequence of evaluate return values:
    # 1. logged_in check -> True
    # 2. expand sidebar -> None
    # 3. collect sidebar items (reaches max_chats=1 and breaks)
    # 4. scroll conversation container -> None
    # 5. extract messages
    mock_page.evaluate = AsyncMock(
        side_effect=[
            True,
            None,
            [
                {
                    "id": "chat99999999",
                    "url": "https://gemini.google.com/app/chat99999999",
                    "title": "Mocked Sidebar Chat",
                }
            ],
            None,
            {
                "pageTitle": "Mocked Sidebar Chat",
                "messages": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello!"},
                ],
            },
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
        res = await sync_from_browser(max_chats=1, login_timeout_sec=5, base_dir=tmp_path)

    assert res["status"] == "ok"
    assert res["synced_count"] == 1
    assert res["synced_chats"][0]["chat_id"] == "chat99999999"
