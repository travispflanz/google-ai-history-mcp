import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from google_ai_history.cli import main as cli_main
from google_ai_history.importers import (
    _detect_url_surface_and_id,
    _fetch_public_share_via_http,
    _harvest_gemini_turns_from_page,
    _launch_chrome_visible,
    _normalize_google_ai_url,
    _probe_profile_google_cookies,
    _strip_directional_chars,
    check_system_health,
    fetch_chat_from_url,
    find_chrome_executable,
    setup_browser_auth,
    sync_from_browser,
)


def test_find_chrome_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = find_chrome_executable()
    assert res is None or isinstance(res, str)
    called: list[Any] = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: called.append((a, kw)))
    _launch_chrome_visible("chrome", tmp_path, "https://gemini.google.com/app")
    assert len(called) == 1


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
        _detect_url_surface_and_id("https://share.gemini.google/RknF2GkWxLcp")[0]
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

    def _fake_evaluate(script: Any, *args: Any, **kwargs: Any) -> Any:
        """Dispatch by script content so the mock survives call-order changes."""
        s = script if isinstance(script, str) else ""
        if "pageTitle" in s:
            return {
                "pageTitle": "SaaS Billing Idea",
                "messages": [
                    {"role": "user", "content": "Let's sketch a SaaS billing webhook handler."},
                    {"role": "assistant", "content": "Here is the FastAPI Stripe webhook design."},
                ],
            }
        if "infinite-scroller.chat-history" in s:
            return {"top": 0, "height": 0, "client": 0}
        if "user-query, model-response" in s:
            return []
        if "autoClick" in s:
            return True
        if "data-message-author-role" in s:
            return True
        return None

    mock_page.evaluate = AsyncMock(side_effect=_fake_evaluate)

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
async def test_harvest_gemini_turns_and_cookie_probe(tmp_path: Path) -> None:
    # 1. Test virtualised multi-window scroller harvest
    scroll_pos = 1000
    windows = [
        [
            {"role": "user", "content": "Opening turn 1"},
            {"role": "assistant", "content": "Opening response 1"},
        ],
        [
            {"role": "assistant", "content": "Opening response 1"},  # overlap to test dedup
            {"role": "user", "content": "Closing turn 2"},
            {"role": "assistant", "content": "Closing response 2"},
        ],
    ]
    win_idx = 0

    async def _eval(script: str, frac: float = 0.0) -> Any:
        nonlocal scroll_pos, win_idx
        if "infinite-scroller.chat-history" in script:
            if frac < 0:
                scroll_pos = 0
                return {"top": 0, "height": 1000, "client": 500}
            scroll_pos += 500
            return {"top": scroll_pos, "height": 1000, "client": 500}
        cur = windows[min(win_idx, len(windows) - 1)]
        win_idx += 1
        return cur

    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(side_effect=_eval)
    harvested = await _harvest_gemini_turns_from_page(mock_page)
    assert len(harvested) == 4
    assert harvested[0]["content"] == "Opening turn 1"
    assert harvested[-1]["content"] == "Closing response 2"

    # 2. Test SQLite cookie probe (0 when empty, >0 when SID/__Secure-1PSID present)
    profile_dir = tmp_path / "chrome_profile"
    assert _probe_profile_google_cookies(profile_dir) == 0
    net_dir = profile_dir / "Default" / "Network"
    net_dir.mkdir(parents=True)
    cookie_db = net_dir / "Cookies"
    conn = sqlite3.connect(str(cookie_db))
    conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    conn.execute("INSERT INTO cookies VALUES ('.google.com', 'NID', 'anon')")
    conn.commit()
    conn.close()
    assert _probe_profile_google_cookies(profile_dir) == 0

    conn = sqlite3.connect(str(cookie_db))
    conn.execute("INSERT INTO cookies VALUES ('.google.com', '__Secure-1PSID', 'auth_val')")
    conn.execute("INSERT INTO cookies VALUES ('.google.com', 'SID', 'sid_val')")
    conn.commit()
    conn.close()
    assert _probe_profile_google_cookies(profile_dir) == 2


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
            False,
            {"top": 0, "height": 0, "client": 0},
            [],
            {"top": 0, "height": 0, "client": 0},
            [],
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


def test_normalize_url_and_strip_directional() -> None:
    assert (
        _normalize_google_ai_url("share.gemini.google/RknF2GkWxLcp")
        == "https://share.gemini.google/RknF2GkWxLcp"
    )
    assert (
        _normalize_google_ai_url("gemini.google.com/app/abc123")
        == "https://gemini.google.com/app/abc123"
    )
    assert _strip_directional_chars("\u200eGoogle Gemini\u200f") == "Google Gemini"


@pytest.mark.asyncio
async def test_public_share_http_fast_path_and_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inner_payload = [
        [
            None,
            [
                [
                    ["c_1", "r_1"],
                    None,
                    [["User prompt via share RPC", None, None, None, [[]]]],
                    [[["rc_1", ["Assistant markdown response via share RPC"]]]],
                ]
            ],
            [True, "\u200eShared Chat Title"],
        ]
    ]
    rpc_line = json.dumps(
        [["wrb.fr", "ujx1Bf", json.dumps(inner_payload), None, None, None, "generic"]]
    )
    rpc_body = f")]}}'\n\n123\n{rpc_line}\n".encode()

    class DummyResp:
        def __init__(self, body: bytes, url: str) -> None:
            self._body = body
            self._url = url
            self.status = 200

        def read(self) -> bytes:
            return self._body

        def geturl(self) -> str:
            return self._url

        def __enter__(self) -> "DummyResp":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    def fake_urlopen(req: Any, timeout: float = 15) -> DummyResp:
        url_str = req.full_url if hasattr(req, "full_url") else str(req)
        if "batchexecute" in url_str:
            return DummyResp(rpc_body, url_str)
        html = b'<script>window.WIZ_global_data = {"cfb2h":"bl_test","FdrFJe":"123"};</script>'
        return DummyResp(html, "https://gemini.google.com/share/93ab2840ce41")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    parsed = _fetch_public_share_via_http("https://share.gemini.google/RknF2GkWxLcp")
    assert parsed is not None
    assert parsed["title"] == "Shared Chat Title"
    assert len(parsed["messages"]) == 2

    res = await fetch_chat_from_url(
        "share.gemini.google/RknF2GkWxLcp",
        base_dir=tmp_path,
    )
    assert res["status"] == "ok"
    assert res["method"] == "http_direct"
    assert res["chat"]["title"] == "Shared Chat Title"

    health = check_system_health(base_dir=tmp_path)
    assert health["python_supported"] is True
    assert health["public_share_links_ready"] is True

    monkeypatch.setattr(sys, "argv", ["google-ai-history", "doctor"])
    cli_main()
    doc_out = json.loads(capsys.readouterr().out)
    assert doc_out["public_share_links_ready"] is True

    with patch(
        "google_ai_history.importers.sync_from_browser",
        new=AsyncMock(return_value={"status": "ok"}),
    ):
        setup_res = await setup_browser_auth(base_dir=tmp_path)
        assert setup_res["status"] == "authenticated"
