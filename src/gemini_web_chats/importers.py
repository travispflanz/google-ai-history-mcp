import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from gemini_web_chats.db import ensure_dirs, upsert_chat


def clean_html_to_text(html_str: str) -> str:
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "html.parser")
    return soup.get_text("\n", strip=True)


def parse_takeout_json_items(
    items: list[dict[str, Any]],
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Groups Google Takeout MyActivity.json items into conversation threads."""
    parsed_entries: list[dict[str, str]] = []
    for item in items:
        title_raw = str(item.get("title", ""))
        time_str = str(item.get("time", ""))
        prompt = re.sub(r"^Prompted\s+", "", title_raw, flags=re.IGNORECASE).strip()

        response_parts: list[str] = []
        for safe_item in item.get("safeHtmlItem", []):
            if isinstance(safe_item, dict):
                html_content = str(safe_item.get("html", ""))
                cleaned = clean_html_to_text(html_content)
                if cleaned:
                    response_parts.append(cleaned)
        for sub in item.get("subtitles", []):
            if isinstance(sub, dict):
                sub_val = str(sub.get("name", ""))
                if sub_val:
                    response_parts.append(clean_html_to_text(sub_val))

        response_text = "\n\n".join(response_parts).strip()
        if not prompt and not response_text:
            continue

        parsed_entries.append(
            {
                "prompt": prompt or "(Prompt)",
                "response": response_text or "(No response text recorded in Takeout)",
                "timestamp": time_str,
            }
        )

    parsed_entries.sort(key=lambda x: x.get("timestamp", ""))

    threads: list[list[dict[str, str]]] = []
    current_thread: list[dict[str, str]] = []
    last_dt: datetime | None = None

    for entry in parsed_entries:
        ts_str = entry["timestamp"]
        dt = None
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                dt = None

        if current_thread and dt and last_dt and abs((dt - last_dt).total_seconds()) > 2700:
            threads.append(current_thread)
            current_thread = []

        current_thread.append(entry)
        if dt:
            last_dt = dt

    if current_thread:
        threads.append(current_thread)

    saved: list[dict[str, Any]] = []
    for thread in threads:
        first_prompt = thread[0]["prompt"]
        title = first_prompt[:80].splitlines()[0] if first_prompt else "Takeout Gemini Thread"
        first_ts = thread[0]["timestamp"]
        last_ts = thread[-1]["timestamp"]
        hash_id = hashlib.sha1(f"{first_ts}:{first_prompt}".encode()).hexdigest()[:16]
        chat_id = f"takeout_{hash_id}"

        messages: list[dict[str, Any]] = []
        for turn in thread:
            messages.append(
                {
                    "role": "user",
                    "content": turn["prompt"],
                    "timestamp": turn["timestamp"],
                }
            )
            if turn["response"]:
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn["response"],
                        "timestamp": turn["timestamp"],
                    }
                )

        res = upsert_chat(
            chat_id=chat_id,
            title=title,
            messages=messages,
            source="takeout",
            created_at=first_ts,
            updated_at=last_ts,
            base_dir=base_dir,
        )
        saved.append(res)
    return saved


def parse_takeout_html(
    html_text: str,
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Parses Google Takeout MyActivity.html for Gemini Apps."""
    soup = BeautifulSoup(html_text, "html.parser")
    cells = soup.select("div.outer-cell")
    items: list[dict[str, Any]] = []
    for cell in cells:
        content_cells = cell.select("div.content-cell")
        if not content_cells:
            continue
        main_html = content_cells[0]
        lines = [line.strip() for line in main_html.get_text("\n").splitlines() if line.strip()]
        if not lines:
            continue
        prompt = re.sub(r"^Prompted\s+", "", lines[0], flags=re.IGNORECASE).strip()
        timestamp = lines[-1] if len(lines) > 1 else ""
        response = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else ""
        items.append(
            {
                "title": f"Prompted {prompt}",
                "time": timestamp,
                "safeHtmlItem": [{"html": response}],
            }
        )
    return parse_takeout_json_items(items, base_dir=base_dir)


def import_from_path(
    target_path: str | Path,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Imports Gemini chats from Takeout (.zip, .json, .html) or exported JSON/MD directory."""
    p = Path(target_path).expanduser().resolve()
    if not p.exists():
        return {"error": f"Path does not exist: {p}"}

    imported_chats: list[dict[str, Any]] = []

    if p.is_file() and p.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(p, "r") as zf:
                zf.extractall(tmpdir)
            return import_from_path(tmpdir, base_dir=base_dir)

    files_to_scan = [p] if p.is_file() else list(p.rglob("*"))
    for f in files_to_scan:
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue

            if (
                isinstance(data, list)
                and data
                and isinstance(data[0], dict)
                and ("safeHtmlItem" in data[0] or "header" in data[0] or "title" in data[0])
            ):
                imported_chats.extend(parse_takeout_json_items(data, base_dir=base_dir))
            elif isinstance(data, dict) and ("messages" in data or "turns" in data):
                raw_msgs = data.get("messages") or data.get("turns") or []
                chat_id = str(data.get("id") or data.get("chat_id") or f"json_{f.stem}")
                title = str(data.get("title") or f.stem)
                msgs: list[dict[str, Any]] = []
                for m in raw_msgs:
                    if isinstance(m, dict):
                        role = str(m.get("role") or m.get("author") or "user")
                        content = str(m.get("content") or m.get("text") or "")
                        msgs.append(
                            {
                                "role": role,
                                "content": content,
                                "timestamp": m.get("timestamp"),
                            }
                        )
                if msgs:
                    imported_chats.append(
                        upsert_chat(
                            chat_id=chat_id,
                            title=title,
                            messages=msgs,
                            source="json_export",
                            base_dir=base_dir,
                        )
                    )
        elif ext in (".html", ".htm") and "activity" in f.name.lower():
            html_text = f.read_text(encoding="utf-8", errors="replace")
            imported_chats.extend(parse_takeout_html(html_text, base_dir=base_dir))
        elif ext == ".md":
            text = f.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                chat_id = f"md_{hashlib.sha1(str(f).encode()).hexdigest()[:12]}"
                first_line = text.splitlines()[0].lstrip("# ").strip() or f.stem
                imported_chats.append(
                    upsert_chat(
                        chat_id=chat_id,
                        title=first_line[:80],
                        messages=[{"role": "assistant", "content": text}],
                        source="markdown_import",
                        base_dir=base_dir,
                    )
                )

    return {
        "imported_count": len(imported_chats),
        "chats": imported_chats[:50],
    }


def find_chrome_executable() -> str | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


async def sync_from_browser(
    max_chats: int = 50,
    login_timeout_sec: int = 180,
    cdp_url: str = "http://127.0.0.1:9222",
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Connects to Chrome over CDP and scrapes gemini.google.com sidebar chats."""
    from playwright.async_api import async_playwright

    paths = ensure_dirs(base_dir)

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception:
            chrome_exe = find_chrome_executable()
            if not chrome_exe:
                return {"error": "Could not find Chrome or Edge executable on this machine."}
            subprocess.Popen(
                [
                    chrome_exe,
                    "--remote-debugging-port=9222",
                    f"--user-data-dir={paths['profile']}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "https://gemini.google.com/app",
                ]
            )
            for _ in range(30):
                await asyncio.sleep(1.0)
                try:
                    browser = await p.chromium.connect_over_cdp(cdp_url)
                    break
                except Exception:
                    continue

        if not browser:
            return {"error": "Failed to connect to Chrome over CDP port 9222."}

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = None
        for pg in context.pages:
            if "gemini.google.com" in pg.url:
                page = pg
                break
        if not page:
            page = await context.new_page()
            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")

        deadline = time.time() + login_timeout_sec
        logged_in = False
        while time.time() < deadline:
            url = page.url
            if "accounts.google.com" not in url and "gemini.google.com" in url:
                has_chat_ui = await page.evaluate(
                    """() => {
                        const signInBtn = Array.from(document.querySelectorAll('a, button')).find(
                            el => /^sign in$/i.test((el.innerText || '').trim())
                        );
                        const hasInputOrSidebar = !!(
                            document.querySelector(
                                'rich-textarea, textarea, [contenteditable="true"], ' +
                                'conversations-list, [data-test-id*="conversation"]'
                            )
                        );
                        return !signInBtn && hasInputOrSidebar;
                    }"""
                )
                if has_chat_ui:
                    logged_in = True
                    break
            await asyncio.sleep(2.0)

        if not logged_in:
            return {
                "status": "login_required",
                "message": (
                    "Please sign into your Google account in the opened Chrome window "
                    "and run sync_gemini_chats_from_browser again."
                ),
            }

        await page.evaluate(
            """() => {
                const menuBtn = document.querySelector(
                    'button[aria-label*="Main menu" i], button[aria-label*="Menu" i], ' +
                    'button[data-test-id="side-nav-menu-button"]'
                );
                const listEl = document.querySelector(
                    'conversations-list, [data-test-id="all-conversations"]'
                );
                if (menuBtn && (!listEl || listEl.getBoundingClientRect().width < 40)) {
                    menuBtn.click();
                }
            }"""
        )
        await asyncio.sleep(2.0)

        collected_chats: list[dict[str, str]] = []
        seen_ids: set[str] = set()

        for _ in range(25):
            items = await page.evaluate(
                """() => {
                    const out = [];
                    document.querySelectorAll('a[href*="/app/"]').forEach(a => {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/\\/app\\/([a-zA-Z0-9_-]{8,})/);
                        if (m) {
                            out.push({
                                id: m[1],
                                url: new URL(href, location.origin).href,
                                title: (a.innerText || a.getAttribute('aria-label') || '').trim()
                            });
                        }
                    });
                    const convSelector =
                        '[data-test-id="conversation"], ' +
                        '.conversation-items-container [role="button"], ' +
                        'conversations-list [role="listitem"]';
                    document.querySelectorAll(convSelector).forEach(el => {
                        const text = (el.innerText || '').trim().split('\\n')[0];
                        const jslog = el.getAttribute('jslog') || '';
                        const idMatch =
                            jslog.match(/c_([a-zA-Z0-9_-]{8,})/) ||
                            jslog.match(/([a-f0-9]{16})/);
                        if (idMatch && text) {
                            out.push({
                                id: idMatch[1],
                                url: `https://gemini.google.com/app/${idMatch[1]}`,
                                title: text
                            });
                        }
                    });
                    return out;
                }"""
            )
            new_found = 0
            for item in items:
                cid = item["id"]
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    collected_chats.append(item)
                    new_found += 1

            if len(collected_chats) >= max_chats:
                break

            scrolled = await page.evaluate(
                """() => {
                    const moreBtn = Array.from(document.querySelectorAll('button, span')).find(
                        el => /show more|load more/i.test((el.innerText || '').trim())
                    );
                    if (moreBtn) {
                        moreBtn.click();
                        return true;
                    }
                    const scrollers = document.querySelectorAll(
                        'conversations-list, infinite-scroller, nav, [role="navigation"]'
                    );
                    let didScroll = false;
                    scrollers.forEach(s => {
                        if (s.scrollHeight > s.clientHeight) {
                            s.scrollTop = s.scrollHeight;
                            didScroll = true;
                        }
                    });
                    return didScroll;
                }"""
            )
            await asyncio.sleep(1.5)
            if not scrolled and new_found == 0:
                break

        synced_results: list[dict[str, Any]] = []
        for chat_meta in collected_chats[:max_chats]:
            cid = chat_meta["id"]
            url = chat_meta["url"]
            title_hint = chat_meta["title"] or f"Gemini Chat {cid}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2.5)

                await page.evaluate(
                    """() => {
                        const scroller = document.querySelector(
                            'infinite-scroller, main, .chat-history'
                        );
                        if (scroller) {
                            scroller.scrollTop = 0;
                        }
                    }"""
                )
                await asyncio.sleep(1.0)

                extracted = await page.evaluate(
                    """() => {
                        const messages = [];
                        const turnNodes = document.querySelectorAll(
                            'user-query, model-response, ' +
                            '[data-message-author-role], .conversation-container'
                        );
                        if (turnNodes.length > 0) {
                            turnNodes.forEach(node => {
                                const tag = node.tagName.toLowerCase();
                                const roleAttr = node.getAttribute('data-message-author-role');
                                if (tag === 'user-query' || roleAttr === 'user') {
                                    const txt = (node.innerText || '').trim();
                                    if (txt) messages.push({ role: 'user', content: txt });
                                } else if (tag === 'model-response' || roleAttr === 'model') {
                                    const md = node.querySelector(
                                        'message-content, .markdown, .model-response-text'
                                    ) || node;
                                    const txt = (md.innerText || '').trim();
                                    if (txt) messages.push({ role: 'assistant', content: txt });
                                }
                            });
                        }
                        const titleEl = document.querySelector(
                            '[data-test-id="conversation-title"], .conversation-title'
                        );
                        const pageTitle = (titleEl?.innerText || document.title || '')
                            .replace(/^Gemini\\s*[-–—]\\s*/i, '')
                            .trim();
                        return { pageTitle, messages };
                    }"""
                )

                msgs = extracted.get("messages") or []
                final_title = extracted.get("pageTitle") or title_hint
                if msgs:
                    saved = upsert_chat(
                        chat_id=cid,
                        title=final_title,
                        messages=msgs,
                        source="browser",
                        url=url,
                        base_dir=base_dir,
                    )
                    synced_results.append(saved)
            except Exception as exc:
                synced_results.append({"chat_id": cid, "error": str(exc)})

        return {
            "status": "ok",
            "discovered_in_sidebar": len(collected_chats),
            "synced_count": len([r for r in synced_results if "markdown_path" in r]),
            "synced_chats": synced_results,
        }
