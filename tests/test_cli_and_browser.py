import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from google_ai_history.cli import main as cli_main
from google_ai_history.importers import (
    _canonicalize_aimode_url,
    _check_registered_mcp_configs,
    _clean_gemini_title,
    _detect_url_surface_and_id,
    _extract_aistudio_from_page,
    _extract_gemini_chat_from_page,
    _fetch_public_share_via_http,
    _harvest_gemini_turns_from_page,
    _launch_chrome_visible,
    _normalize_google_ai_url,
    _parse_myactivity_dialog_text,
    _parse_surface_timestamp,
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
async def test_fetch_chat_from_url_mocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    bad_url = await fetch_chat_from_url("not-a-url", base_dir=tmp_path)
    assert "error" in bad_url

    mock_page = AsyncMock()
    mock_page.url = "https://gemini.google.com/app/chat_url_123"

    def _fake_evaluate(script: Any, *args: Any, **kwargs: Any) -> Any:
        """Dispatch by script content so the mock survives call-order changes."""
        s = script if isinstance(script, str) else ""
        if "hasDeepResearchCard" in s:
            return {
                "pageTitle": "SaaS Billing Idea - Google Gemini",
                "titleElText": "",
                "hasDeepResearchCard": False,
                "messages": [
                    {"role": "user", "content": "Let's sketch a SaaS billing webhook handler."},
                    {"role": "assistant", "content": "Here is the FastAPI Stripe webhook design."},
                ],
            }
        if "deep-research-immersive-panel" in s:
            return None
        if "immersive-entry-chip" in s:
            return False
        if "infinite-scroller.chat-history" in s:
            return {"top": 0, "height": 0, "client": 0}
        if "data-message-author-role" in s:
            return True
        if "user-query, model-response" in s:
            return []
        if "autoClick" in s:
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
async def test_sync_from_browser_all_targets_mocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    mock_page = AsyncMock()
    mock_page.url = "https://gemini.google.com/app"

    def _eval_dispatch(script: Any, *args: Any, **kwargs: Any) -> Any:
        s = script if isinstance(script, str) else ""
        if "autoClick" in s:
            return True
        if "side-nav-sparkle-button" in s:
            return None
        if "convSelector" in s:
            return [
                {
                    "id": "chat99999999",
                    "url": "https://gemini.google.com/app/chat99999999",
                    "title": "Mocked Sidebar Chat",
                }
            ]
        if "didScroll" in s:
            return False
        if "infinite-scroller.chat-history" in s:
            return {"top": 0, "height": 0, "client": 0}
        if "hasDeepResearchCard" in s:
            return {
                "pageTitle": "Mocked Sidebar Chat - Google Gemini",
                "titleElText": "",
                "hasDeepResearchCard": False,
                "messages": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello!"},
                ],
            }
        if "deep-research-immersive-panel" in s:
            return None
        if "immersive-entry-chip" in s:
            return False
        if "user-query, model-response" in s:
            return []
        if "AI Mode history" in s:
            return None
        if "button[data-thread-id]" in s:
            return [
                {
                    "url": "https://www.google.com/search?udm=50&q=rust+async",
                    "title": "Rust Async Traits",
                }
            ]
        if "AI Mode Conversation:" in s:
            return [{"role": "assistant", "content": "Async functions in traits are stabilized."}]
        if 'a[href*="/prompts/"]' in s:
            return [
                {
                    "id": "prompt_abc_123",
                    "url": "https://aistudio.google.com/prompts/prompt_abc_123",
                    "title": "Prompt Engineering Test",
                    "updatedText": "2 days ago",
                }
            ]
        if "parseFileChunk" in s:
            return {
                "pageTitle": "Prompt Engineering Test",
                "turns": [
                    {
                        "id": "t1",
                        "role": "user",
                        "text": "Test prompt",
                        "thought": "",
                        "attachments": [],
                    },
                    {
                        "id": "t2",
                        "role": "assistant",
                        "text": "Test model output",
                        "thought": "",
                        "attachments": [],
                    },
                ],
                "attachments": [],
            }
        if "ms-autoscroll-container" in s:
            return {"top": 0, "height": 0, "client": 0}
        if 'a[href*="/notebook/"]' in s:
            return [
                {
                    "id": "nb_xyz_789",
                    "url": "https://notebooklm.google.com/notebook/nb_xyz_789",
                    "title": "AI Architecture Notebook",
                }
            ]
        if "document.querySelector('main')" in s:
            return "Summary of AI architecture sources and notes."
        if "data-gah-idx" in s and "maxItems" in s:
            if "myactivity.google.com/product/gemini" in str(mock_page.goto.call_args):
                return [
                    {
                        "idx": 0,
                        "title": "Prompted How does Google My Activity centralize AI logs?",
                        "body": "It stores Gemini, Search AI Mode, Maps, and Assistant logs.",
                        "dataDate": "20261103",
                        "timeStr": "11:02 AM",
                        "hasDetailsBtn": False,
                    },
                    {
                        "idx": 1,
                        "title": "Prompted Duplicate prompt",
                        "body": "Dup body",
                        "dataDate": "20261103",
                        "timeStr": "11:05 AM",
                        "hasDetailsBtn": False,
                    },
                ]
            return [
                {
                    "idx": 0,
                    "title": "Asked in AI Mode Best local vector database",
                    "body": "SQLite FTS5 and sqlite-vec are great local options.",
                    "dataDate": "20261103",
                    "timeStr": "12:00 PM",
                    "hasDetailsBtn": False,
                }
            ]
        return None

    mock_page.evaluate = AsyncMock(side_effect=_eval_dispatch)

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
            max_chats=2,
            login_timeout_sec=5,
            target="all",
            base_dir=tmp_path,
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[google-ai-history]" in captured.err
    assert res["status"] == "ok"
    assert res["synced_count"] == 7
    assert "surfaces" in res
    assert res["surfaces"]["gemini"]["synced_count"] == 1
    assert res["surfaces"]["myactivity"]["synced_count"] == 3

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

    monkeypatch.setattr(sys, "argv", ["google-ai-history", "list"])
    cli_main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["google-ai-history", "search", "test"])
    cli_main()
    capsys.readouterr()

    with patch(
        "google_ai_history.importers.sync_from_browser",
        new=AsyncMock(return_value={"status": "ok"}),
    ):
        setup_res = await setup_browser_auth(base_dir=tmp_path)
        assert setup_res["status"] == "authenticated"


@pytest.mark.asyncio
async def test_fetch_cookie_mismatch_and_search_ai_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    mock_page = AsyncMock()
    mock_page.url = "https://accounts.google.com/CookieMismatch"
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={"pageTitle": "", "messages": []})

    mock_context = AsyncMock()
    mock_context.pages = [mock_page]

    mock_browser = AsyncMock()
    mock_browser.contexts = [mock_context]

    with patch("playwright.async_api.async_playwright") as mock_pw:
        pw_instance = AsyncMock()
        mock_pw.return_value.__aenter__.return_value = pw_instance
        pw_instance.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

        res = await fetch_chat_from_url(
            "https://gemini.google.com/app/5d10d15809df56af",
            base_dir=tmp_path,
        )
        assert res["status"] == "error"
        assert res["reason"] == "cookie_mismatch"
        assert "CookieMismatch" in res["error"]

        # 2. Test successful Search AI Mode browser fetch with canonical URL
        mock_page.url = "https://www.google.com/search?udm=50&q=test"
        mock_page.evaluate = AsyncMock(
            side_effect=[
                True,  # has_turns
                {
                    "pageTitle": "test query",
                    "messages": [
                        {"role": "user", "content": "test query"},
                        {"role": "assistant", "content": "test answer"},
                    ],
                },
            ]
        )
        res_ai = await fetch_chat_from_url(
            "https://www.google.com/search?udm=50&sxsrf=abc&mtid=MTID123&ved=xyz&q=test",
            base_dir=tmp_path,
        )
        assert res_ai["status"] == "ok"
        assert res_ai["chat"]["chat_id"] == "aimode_MTID123"
        assert res_ai["chat"]["url"] == "https://www.google.com/search?udm=50&mtid=MTID123&q=test"
        assert res_ai["chat"]["title"] == "test query"
        assert len(res_ai["chat"]["messages"]) == 2


def test_mcp_config_doctor_title_url_and_timestamps(tmp_path: Path) -> None:
    bad_cfg = tmp_path / "mcp_config.json"
    bad_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "google-ai-history": {
                        "command": "uv",
                        "args": ["run", "--directory", str(tmp_path / "missing_dir"), "serve"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    good_dir = tmp_path / "repo"
    good_dir.mkdir()
    good_cfg = tmp_path / "good_mcp.json"
    good_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "google-ai-history": {
                        "command": sys.executable,
                        "args": ["--directory", str(good_dir), "-m", "google_ai_history"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    checked = _check_registered_mcp_configs([bad_cfg, good_cfg])
    assert len(checked) == 2
    assert checked[0]["ok"] is False
    assert any("--directory" in i for i in checked[0]["issues"])
    assert checked[1]["ok"] is True

    # Title cleaning (Issue #6)
    assert (
        _clean_gemini_title("MFL Custom API Tools and Scripts - Google Gemini")
        == "MFL Custom API Tools and Scripts"
    )
    assert _clean_gemini_title("Conversation with Gemini") == ""
    assert _clean_gemini_title("Google Gemini") == ""

    # Canonical AI Mode URLs (Issue #8)
    noisy_url = (
        "https://www.google.com/search?udm=50&atvm=2&sxsrf=AKL5yDiLk"
        "&mstk=AUtExf&mtid=NJXvavKuCq7oyckPisawiQ8&csuir=1&aep=26&q=best+nfl+qbs+2026&ved=0CAEQ"
    )
    canon = _canonicalize_aimode_url(noisy_url)
    assert canon == (
        "https://www.google.com/search?udm=50&mtid=NJXvavKuCq7oyckPisawiQ8&q=best+nfl+qbs+2026"
    )
    assert _detect_url_surface_and_id(noisy_url) == (
        "browser_search_ai_mode",
        "aimode_NJXvavKuCq7oyckPisawiQ8",
    )

    # Timestamp parsing (Issue #7)
    ref = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    ts_oct = _parse_surface_timestamp("Oct 26, 1:19 PM", now=ref)
    assert ts_oct is not None and ts_oct.startswith("2026-10-26")
    ts_nov = _parse_surface_timestamp("November 3 at 11:02 AM", data_date="20261103", now=ref)
    assert ts_nov is not None and ts_nov.startswith("2026-11-03")
    ts_rel = _parse_surface_timestamp("9 months ago", now=ref)
    assert ts_rel is not None and ts_rel.startswith("2026-02")


@pytest.mark.asyncio
async def test_gemini_deep_research_and_incomplete_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    mock_page = AsyncMock()

    # 1. Deep Research panel captured
    def _eval_captured(script: Any, *args: Any) -> Any:
        s = script if isinstance(script, str) else ""
        if "hasDeepResearchCard" in s:
            return {
                "pageTitle": "MFL Custom API Tools and Scripts - Google Gemini",
                "titleElText": "Argon",
                "hasDeepResearchCard": True,
                "messages": [
                    {"role": "user", "content": "Do deep research"},
                    {
                        "role": "assistant",
                        "content": (
                            "I've completed your research.\n"
                            "MyFantasyLeague Custom Tools Research\nOct 26, 1:19 PM"
                        ),
                    },
                ],
            }
        if "infinite-scroller.chat-history" in s:
            return {"top": 0, "height": 0, "client": 0}
        if "user-query, model-response" in s:
            return []
        if "deep-research-immersive-panel" in s:
            return {
                "text": "Architectural Blueprint for MyFantasyLeague " + ("x" * 120),
                "length": 160,
            }
        return None

    mock_page.evaluate = AsyncMock(side_effect=_eval_captured)
    res_ok = await _extract_gemini_chat_from_page(mock_page)
    assert res_ok["title"] == "MFL Custom API Tools and Scripts"
    assert res_ok["incomplete"] is None
    assert res_ok["messages"][-1]["kind"] == "deep_research_report"
    assert res_ok["messages"][-1]["timestamp"] is not None

    # 2. Deep Research card detected, panel fails to open -> incomplete flag
    def _eval_missing(script: Any, *args: Any) -> Any:
        s = script if isinstance(script, str) else ""
        if "hasDeepResearchCard" in s:
            return {
                "pageTitle": "MFL Custom API Tools and Scripts - Google Gemini",
                "titleElText": "",
                "hasDeepResearchCard": True,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "I've completed your research.\nOct 26, 1:19 PM",
                    }
                ],
            }
        if "infinite-scroller.chat-history" in s:
            return {"top": 0, "height": 0, "client": 0}
        if "user-query, model-response" in s:
            return []
        if "deep-research-immersive-panel" in s:
            return None
        if "immersive-entry-chip" in s:
            return True
        return None

    mock_page.evaluate = AsyncMock(side_effect=_eval_missing)
    res_inc = await _extract_gemini_chat_from_page(mock_page)
    assert res_inc["incomplete"] == "deep_research_report_not_captured"


@pytest.mark.asyncio
async def test_aistudio_regression_and_myactivity_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    # Regression test mirroring aistudio_12fr5mIS-NPKBw86QVyf9_CInmjbncTNN
    mock_page = AsyncMock()
    scroll_calls = 0

    def _eval_aistudio(script: Any, *args: Any) -> Any:
        nonlocal scroll_calls
        s = script if isinstance(script, str) else ""
        if "parseFileChunk" in s:
            if scroll_calls == 0:
                return {
                    "pageTitle": "Fantasy Football Free Agent Claims",
                    "turns": [
                        {
                            "id": "turn-0",
                            "role": "user",
                            "text": "",
                            "thought": "",
                            "attachments": [
                                {
                                    "name": "2025_KC_Keeper_Football_Rules.pdf",
                                    "tokens": "6,161 tokens",
                                    "drive_id": "1DriveRuleDocId123",
                                    "url": "https://drive.google.com/file/d/1DriveRuleDocId123/view",
                                }
                            ],
                        },
                        {
                            "id": "turn-1",
                            "role": "user",
                            "text": "What is our current fantasy football free agent claims order?",
                            "thought": "",
                            "attachments": [],
                        },
                    ],
                    "attachments": [
                        {
                            "name": "2025_KC_Keeper_Football_Rules.pdf",
                            "tokens": "6,161 tokens",
                            "drive_id": "1DriveRuleDocId123",
                            "url": "https://drive.google.com/file/d/1DriveRuleDocId123/view",
                        }
                    ],
                }
            return {
                "pageTitle": "Fantasy Football Free Agent Claims",
                "turns": [
                    {
                        "id": "turn-2",
                        "role": "assistant",
                        "text": "",
                        "thought": "Expand to view model thoughts",
                        "attachments": [],
                    },
                    {
                        "id": "turn-3",
                        "role": "assistant",
                        "text": "Based on the league rules, rolling waiver priority applies.",
                        "thought": "",
                        "attachments": [],
                    },
                ],
                "attachments": [],
            }
        if "sc.scrollTop = 0" in s:
            return None
        if "sc.scrollHeight <= sc.clientHeight" in s:
            scroll_calls += 1
            if scroll_calls == 1:
                return {"top": 500, "height": 1000, "client": 500}
            return {"top": 1000, "height": 1000, "client": 500}
        return None

    mock_page.evaluate = AsyncMock(side_effect=_eval_aistudio)
    parsed_ais = await _extract_aistudio_from_page(mock_page)
    assert parsed_ais["title"] == "Fantasy Football Free Agent Claims"
    assert len(parsed_ais["attachments"]) == 1
    assert parsed_ais["attachments"][0]["drive_id"] == "1DriveRuleDocId123"
    # Collapsed placeholder thought dropped; attachment turn + user prompt + model reply = 3
    assert len(parsed_ais["messages"]) == 3
    assert parsed_ais["messages"][0]["role"] == "user"
    assert "2025_KC_Keeper_Football_Rules.pdf" in parsed_ais["messages"][0]["content"]
    assert parsed_ais["messages"][1]["role"] == "user"
    assert parsed_ais["messages"][2]["role"] == "assistant"
    assert all("more_vert" not in m["content"] for m in parsed_ais["messages"])

    # My Activity Details dialog parsing (Issue #5)
    dlg_text = (
        "Item details\nmore_vert\nclose\nGemini Apps\n"
        "Prompted Do deep research on MFL API endpoints\n"
        "Details\nevent\nNovember 3 at 11:02\u202fAM\napps\nGemini Apps\n"
        "https://gemini.google.com/app/0342e975959fbbe1\nchat\n"
        "Here is the full MyFantasyLeague API synthesis.\n"
        "Why this activity?\nManage\nLearn more"
    )
    dlg = _parse_myactivity_dialog_text(
        dlg_text,
        ["https://gemini.google.com/app/0342e975959fbbe1"],
        fallback_prompt="Fallback",
    )
    assert dlg["prompt"] == "Do deep research on MFL API endpoints"
    assert dlg["response"] == "Here is the full MyFantasyLeague API synthesis."
    assert dlg["source_chat_id"] == "0342e975959fbbe1"
    assert dlg["source_url"] == "https://gemini.google.com/app/0342e975959fbbe1"
    assert "November 3" in dlg["time_text"]

    # End-to-end My Activity Details click + Gemini backfill in sync_from_browser(target="all")
    ma_page = AsyncMock()
    ma_page.url = "https://gemini.google.com/app"

    def _eval_full(script: Any, *args: Any) -> Any:
        s = script if isinstance(script, str) else ""
        if "autoClick" in s:
            return True
        if "side-nav-sparkle-button" in s:
            return None
        if "convSelector" in s:
            return []
        if "didScroll" in s:
            return False
        if "AI Mode history" in s:
            return None
        if "button[data-thread-id]" in s:
            return []
        if 'a[href*="/prompts/"]' in s:
            return []
        if 'a[href*="/notebook/"]' in s:
            return []
        if "data-gah-idx" in s and "maxItems" in s:
            if "myactivity.google.com/product/gemini" in str(ma_page.goto.call_args):
                return [
                    {
                        "idx": 0,
                        "title": "Prompted Do deep research on MFL...",
                        "body": "",
                        "dataDate": "20261103",
                        "timeStr": "11:02 AM",
                        "hasDetailsBtn": True,
                    }
                ]
            return []
        if "btn.click()" in s and "data-gah-idx" in s:
            return True
        if "aria-modal" in s:
            return {
                "text": dlg_text,
                "links": ["https://gemini.google.com/app/0342e975959fbbe1"],
            }
        if "hasDeepResearchCard" in s:
            return {
                "pageTitle": "MFL Research - Google Gemini",
                "titleElText": "",
                "hasDeepResearchCard": False,
                "messages": [
                    {"role": "user", "content": "Do deep research"},
                    {"role": "assistant", "content": "Report"},
                ],
            }
        if "infinite-scroller.chat-history" in s:
            return {"top": 0, "height": 0, "client": 0}
        if "user-query, model-response" in s:
            return []
        if "deep-research-immersive-panel" in s:
            return None
        if "immersive-entry-chip" in s:
            return False
        return None

    ma_page.evaluate = AsyncMock(side_effect=_eval_full)
    mock_ctx = MagicMock()
    mock_ctx.pages = [ma_page]
    mock_br = MagicMock()
    mock_br.contexts = [mock_ctx]
    mock_pw_inst = AsyncMock()
    mock_pw_inst.chromium.connect_over_cdp = AsyncMock(return_value=mock_br)
    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_inst)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("playwright.async_api.async_playwright", return_value=mock_pw_cm):
        sync_res = await sync_from_browser(max_chats=5, target="all", base_dir=tmp_path)

    assert sync_res["status"] == "ok"
    assert "gemini_from_myactivity" in sync_res["surfaces"]
    assert sync_res["surfaces"]["gemini_from_myactivity"]["synced_count"] == 1


@pytest.mark.asyncio
async def test_error_paths_timestamps_and_mcp_config_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    # Additional timestamp & MCP config edge cases
    ref = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    assert _parse_surface_timestamp("2026-11-01T12:00:00Z") is not None
    assert _parse_surface_timestamp("Yesterday at 10:25 PM", now=ref) is not None
    assert _parse_surface_timestamp("Today at 8:00 AM", now=ref) is not None
    assert _parse_surface_timestamp("1 year ago", now=ref) is not None
    assert _parse_surface_timestamp("3 hours ago", now=ref) is not None
    assert _parse_surface_timestamp("invalid-date") is None

    broken_json = tmp_path / "broken.json"
    broken_json.write_text("{bad", encoding="utf-8")
    no_cmd = tmp_path / "nocmd.json"
    no_cmd.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "google-ai-history": {
                        "command": "",
                        "args": ["--directory"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    missing_exe = tmp_path / "missing_exe.json"
    missing_exe.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "google-ai-history": {
                        "command": str(tmp_path / "nope.exe"),
                        "args": ["serve"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    edge_cfgs = _check_registered_mcp_configs([broken_json, no_cmd, missing_exe])
    assert len(edge_cfgs) == 3
    assert all(not c["ok"] for c in edge_cfgs)

    # sync_from_browser when Chrome is not found
    with (
        patch("google_ai_history.importers.find_chrome_executable", return_value=None),
        patch("playwright.async_api.async_playwright") as mock_pw,
    ):
        pw_inst = AsyncMock()
        mock_pw.return_value.__aenter__.return_value = pw_inst
        pw_inst.chromium.connect_over_cdp = AsyncMock(side_effect=RuntimeError("no cdp"))
        no_chrome = await sync_from_browser(base_dir=tmp_path)
        assert "error" in no_chrome

        no_chrome_fetch = await fetch_chat_from_url(
            "https://gemini.google.com/app/abc12345",
            base_dir=tmp_path,
        )
        assert "error" in no_chrome_fetch

    # fetch_chat_from_url for AI Studio + empty thread fallback
    mock_page = AsyncMock()
    mock_page.url = "https://aistudio.google.com/prompts/p12345678"
    mock_page.evaluate = AsyncMock(
        side_effect=[
            True,  # auth check
            True,  # has_turns
            None,  # scroll to top
            {
                "pageTitle": "AI Studio Direct",
                "turns": [
                    {
                        "id": "t1",
                        "role": "user",
                        "text": "Hello",
                        "thought": "",
                        "attachments": [],
                    },
                    {
                        "id": "t2",
                        "role": "assistant",
                        "text": "World",
                        "thought": "Reasoning step",
                        "attachments": [],
                    },
                ],
                "attachments": [],
            },
            {"top": 0, "height": 0, "client": 0},
        ]
    )
    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_br = MagicMock()
    mock_br.contexts = [mock_ctx]
    with patch("playwright.async_api.async_playwright") as mock_pw:
        pw_inst = AsyncMock()
        mock_pw.return_value.__aenter__.return_value = pw_inst
        pw_inst.chromium.connect_over_cdp = AsyncMock(return_value=mock_br)
        ais_res = await fetch_chat_from_url(
            "https://aistudio.google.com/prompts/p12345678",
            base_dir=tmp_path,
        )
        assert ais_res["status"] == "ok"
        assert ais_res["chat"]["messages"][1]["kind"] == "thought"

        # Empty thread error path
        mock_page.url = "https://gemini.google.com/app/empty_chat_99"
        mock_page.evaluate = AsyncMock(
            side_effect=lambda script, *a: True if "autoClick" in str(script) else None
        )
        empty_res = await fetch_chat_from_url(
            "https://gemini.google.com/app/empty_chat_99",
            base_dir=tmp_path,
        )
        assert empty_res["reason"] == "selector_miss_or_empty_thread"
