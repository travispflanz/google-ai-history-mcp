import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from google_ai_history.cli import main as cli_main
from google_ai_history.importers import (
    _detect_url_surface_and_id,
    fetch_chat_from_url,
    find_chrome_executable,
    sync_from_browser,
)


def test_find_chrome_executable() -> None:
    res = find_chrome_executable()
    assert res is None or isinstance(res, str)


def test_detect_url_surface_and_id() -> None:
    assert (
        _detect_url_surface_and_id("https://gemini.google.com/app/1a2b3c4d5e")[0]
        == "browser_gemini"
    )
    assert (
        _detect_url_surface_and_id("https://gemini.google.com/share/9z8y7x6w")[0]
        == "url_gemini_share"
    )
    assert (
        _detect_url_surface_and_id("https://aistudio.google.com/prompts/p12345678")[0]
        == "browser_aistudio"
    )
    assert (
        _detect_url_surface_and_id("https://notebooklm.google.com/notebook/nb123456")[0]
        == "browser_notebooklm"
    )
    assert (
        _detect_url_surface_and_id("https://www.google.com/search?udm=50&q=test")[0]
        == "browser_search_ai_mode"
    )


def test_cli_import_search_and_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GOOGLE_AI_HISTORY_DIR", str(tmp_path / "data"))
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

    monkeypatch.setattr(sys, "argv", ["google-ai-history", "import", str(export_file)])
    cli_main()
    out = json.loads(capsys.readouterr().out)
    assert out["imported_count"] == 1

    monkeypatch.setattr(sys, "argv", ["google-ai-history", "list"])
    cli_main()
    listed = json.loads(capsys.readouterr().out)
    assert listed["total_chats"] == 1

    monkeypatch.setattr(sys, "argv", ["google-ai-history", "search", "CLI test"])
    cli_main()
    searched = json.loads(capsys.readouterr().out)
    assert searched["matches"] == 1


@pytest.mark.asyncio
async def test_fetch_chat_from_url_mocked(tmp_path: Path) -> None:
    bad_url = await fetch_chat_from_url("not-a-url", base_dir=tmp_path)
    assert "error" in bad_url

    mock_page = AsyncMock()
    mock_page.url = "https://gemini.google.com/app/chat_url_123"
    mock_page.evaluate = AsyncMock(
        side_effect=[
            True,  # logged_in check
            None,  # scroll top
            {
                "pageTitle": "SaaS Billing Idea",
                "messages": [
                    {"role": "user", "content": "Let's sketch a SaaS billing webhook handler."},
                    {"role": "assistant", "content": "Here is the FastAPI Stripe webhook design."},
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
        res = await fetch_chat_from_url(
            "https://gemini.google.com/app/chat_url_123",
            login_timeout_sec=5,
            base_dir=tmp_path,
        )

    assert res["status"] == "ok"
    assert res["chat"]["chat_id"] == "chat_url_123"
    assert res["chat"]["title"] == "SaaS Billing Idea"
    assert len(res["chat"]["messages"]) == 2


@pytest.mark.asyncio
async def test_sync_from_browser_all_targets_mocked(tmp_path: Path) -> None:
    mock_page = AsyncMock()
    mock_page.url = "https://gemini.google.com/app"

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
            None,
            [
                {
                    "url": "https://www.google.com/search?udm=50&q=rust+async",
                    "title": "Rust Async Traits",
                }
            ],
            [{"role": "assistant", "content": "Async functions in traits are stabilized."}],
            [
                {
                    "id": "prompt_abc_123",
                    "url": "https://aistudio.google.com/prompts/prompt_abc_123",
                    "title": "Prompt Engineering Test",
                }
            ],
            [
                {"role": "user", "content": "Test prompt"},
                {"role": "assistant", "content": "Test model output"},
            ],
            [
                {
                    "id": "nb_xyz_789",
                    "url": "https://notebooklm.google.com/notebook/nb_xyz_789",
                    "title": "AI Architecture Notebook",
                }
            ],
            "Summary of AI architecture sources and notes.",
            [
                {
                    "title": "Prompted How does Google My Activity centralize AI logs?",
                    "body": "It stores Gemini, Search AI Mode, Maps, and Assistant logs.",
                }
            ],
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
