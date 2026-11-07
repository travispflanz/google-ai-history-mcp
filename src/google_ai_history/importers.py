import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from google_ai_history.db import ensure_dirs, upsert_chat

SUPPORTED_TARGETS = (
    "gemini",
    "aimode",
    "aistudio",
    "notebooklm",
    "myactivity",
    "all",
)


def clean_html_to_text(html_str: str) -> str:
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "html.parser")
    return soup.get_text("\n", strip=True)


def _classify_takeout_source(item: dict[str, Any]) -> str | None:
    """Determines which Google AI surface a MyActivity item came from, or None if non-AI."""
    header = str(item.get("header", "")).lower()
    title = str(item.get("title", ""))
    title_lower = title.lower()
    products = [str(p).lower() for p in item.get("products", [])]

    if "gemini" in header or "bard" in header or any("gemini" in p for p in products):
        return "takeout_gemini"

    if (
        "ai mode" in header
        or "ai overview" in title_lower
        or "asked in ai mode" in title_lower
        or "circle to search" in title_lower
        or "google lens" in header
    ):
        return "takeout_search_ai_mode"

    if ("maps" in header or "maps" in products) and (
        title_lower.startswith("asked ")
        or title_lower.startswith("prompted ")
        or "ask maps" in title_lower
    ):
        return "takeout_ask_maps"

    if ("youtube" in header or "youtube" in products) and (
        title_lower.startswith("asked ") or title_lower.startswith("prompted ")
    ):
        return "takeout_youtube_ai"

    if "assistant" in header or "assistant" in products:
        if title_lower.startswith(("said ", "asked ", "prompted ")):
            return "takeout_assistant"

    if title_lower.startswith("prompted "):
        return "takeout_gemini"

    return None


def parse_takeout_json_items(
    items: list[dict[str, Any]],
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Groups Takeout MyActivity.json items across all Google AI surfaces."""
    parsed_entries: list[dict[str, str]] = []
    for item in items:
        source_tag = _classify_takeout_source(item)
        if not source_tag:
            continue

        title_raw = str(item.get("title", ""))
        time_str = str(item.get("time", ""))
        prompt = re.sub(
            r"^(Prompted|Asked in AI Mode|Asked Maps|Asked|Said)\s*:?\s*",
            "",
            title_raw,
            flags=re.IGNORECASE,
        ).strip()

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
                "source": source_tag,
                "url": str(item.get("titleUrl", "")),
            }
        )

    parsed_entries.sort(key=lambda x: (x.get("source", ""), x.get("timestamp", "")))

    threads: list[list[dict[str, str]]] = []
    current_thread: list[dict[str, str]] = []
    last_dt: datetime | None = None
    last_source: str | None = None

    for entry in parsed_entries:
        ts_str = entry["timestamp"]
        src = entry["source"]
        dt = None
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                dt = None

        if current_thread and (
            src != last_source or (dt and last_dt and abs((dt - last_dt).total_seconds()) > 2700)
        ):
            threads.append(current_thread)
            current_thread = []

        current_thread.append(entry)
        last_source = src
        if dt:
            last_dt = dt

    if current_thread:
        threads.append(current_thread)

    saved: list[dict[str, Any]] = []
    for thread in threads:
        first_prompt = thread[0]["prompt"]
        src = thread[0]["source"]
        title = first_prompt[:80].splitlines()[0] if first_prompt else f"{src} Thread"
        first_ts = thread[0]["timestamp"]
        last_ts = thread[-1]["timestamp"]
        url = thread[0].get("url") or None
        hash_id = hashlib.sha1(f"{src}:{first_ts}:{first_prompt}".encode()).hexdigest()[:16]
        chat_id = f"{src}_{hash_id}"

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
            source=src,
            url=url,
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
    """Parses Google Takeout MyActivity.html across Google AI surfaces."""
    soup = BeautifulSoup(html_text, "html.parser")
    cells = soup.select("div.outer-cell")
    items: list[dict[str, Any]] = []
    for cell in cells:
        header_el = cell.select_one("div.header-cell")
        header_text = header_el.get_text(" ", strip=True) if header_el else "Gemini Apps"
        content_cells = cell.select("div.content-cell")
        if not content_cells:
            continue
        main_html = content_cells[0]
        lines = [line.strip() for line in main_html.get_text("\n").splitlines() if line.strip()]
        if not lines:
            continue
        first_line = lines[0]
        if not re.match(r"^(Prompted|Asked|Said)", first_line, flags=re.IGNORECASE):
            first_line = f"Prompted {first_line}"
        timestamp = lines[-1] if len(lines) > 1 else ""
        response = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else ""
        items.append(
            {
                "header": header_text,
                "title": first_line,
                "time": timestamp,
                "safeHtmlItem": [{"html": response}],
            }
        )
    return parse_takeout_json_items(items, base_dir=base_dir)


def parse_aistudio_json(
    data: dict[str, Any],
    file_path: Path,
    base_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Parses a Google AI Studio prompt/chat file (from Google Drive 'Google AI Studio' folder)."""
    messages: list[dict[str, Any]] = []
    run_settings = data.get("runSettings") or {}
    model_name = run_settings.get("model") if isinstance(run_settings, dict) else None

    sys_inst = data.get("systemInstruction")
    if isinstance(sys_inst, dict):
        sys_text = sys_inst.get("text") or " ".join(
            str(p.get("text", "")) for p in sys_inst.get("parts", []) if isinstance(p, dict)
        )
        if sys_text.strip():
            messages.append({"role": "system", "content": sys_text.strip()})

    chunked = data.get("chunkedPrompt")
    if isinstance(chunked, dict) and isinstance(chunked.get("chunks"), list):
        for chunk in chunked["chunks"]:
            if not isinstance(chunk, dict):
                continue
            role_raw = str(chunk.get("role", "user")).lower()
            role = "assistant" if role_raw == "model" else role_raw
            text = str(chunk.get("text") or "").strip()
            if chunk.get("isThought") and text:
                text = f"*(Thinking)*\n{text}"
            if text:
                messages.append({"role": role, "content": text})

    elif isinstance(data.get("contents"), list):
        for content_item in data["contents"]:
            if not isinstance(content_item, dict):
                continue
            role_raw = str(content_item.get("role", "user")).lower()
            role = "assistant" if role_raw == "model" else role_raw
            parts = content_item.get("parts") or []
            part_texts = [
                str(p.get("text", "")).strip()
                for p in parts
                if isinstance(p, dict) and p.get("text")
            ]
            combined = "\n\n".join([t for t in part_texts if t])
            if combined:
                messages.append({"role": role, "content": combined})

    if not messages:
        return None

    title = str(data.get("name") or data.get("title") or file_path.stem).strip()
    if model_name:
        title = f"{title} ({model_name})"
    chat_id = f"aistudio_{hashlib.sha1(str(file_path).encode()).hexdigest()[:14]}"
    return upsert_chat(
        chat_id=chat_id,
        title=title,
        messages=messages,
        source="aistudio",
        url="https://aistudio.google.com/library",
        base_dir=base_dir,
    )


def _try_parse_json_file(
    f: Path,
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    try:
        raw_text = f.read_text(encoding="utf-8", errors="replace").strip()
        if not (raw_text.startswith("{") or raw_text.startswith("[")):
            return []
        data = json.loads(raw_text)
    except Exception:
        return []

    imported: list[dict[str, Any]] = []
    if (
        isinstance(data, list)
        and data
        and isinstance(data[0], dict)
        and ("safeHtmlItem" in data[0] or "header" in data[0] or "title" in data[0])
    ):
        imported.extend(parse_takeout_json_items(data, base_dir=base_dir))
    elif isinstance(data, dict) and ("chunkedPrompt" in data or "runSettings" in data):
        res = parse_aistudio_json(data, f, base_dir=base_dir)
        if res:
            imported.append(res)
    elif isinstance(data, dict) and ("messages" in data or "turns" in data):
        raw_msgs = data.get("messages") or data.get("turns") or []
        chat_id = str(data.get("id") or data.get("chat_id") or f"json_{f.stem}")
        title = str(data.get("title") or f.stem)
        source = str(data.get("source") or "json_export")
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
            imported.append(
                upsert_chat(
                    chat_id=chat_id,
                    title=title,
                    messages=msgs,
                    source=source,
                    base_dir=base_dir,
                )
            )
    return imported


def import_from_path(
    target_path: str | Path,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Imports chats from Takeout, Google AI Studio Drive folder, NotebookLM, or JSON/MD dir."""
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
        if ext in (".json", ".prompt", ""):
            imported_chats.extend(_try_parse_json_file(f, base_dir=base_dir))
        elif ext in (".html", ".htm"):
            html_text = f.read_text(encoding="utf-8", errors="replace")
            if "outer-cell" in html_text or "activity" in f.name.lower():
                imported_chats.extend(parse_takeout_html(html_text, base_dir=base_dir))
            elif "notebooklm" in str(f).lower():
                cleaned = clean_html_to_text(html_text)
                if cleaned:
                    chat_id = f"notebooklm_{hashlib.sha1(str(f).encode()).hexdigest()[:12]}"
                    imported_chats.append(
                        upsert_chat(
                            chat_id=chat_id,
                            title=f.stem,
                            messages=[{"role": "assistant", "content": cleaned}],
                            source="notebooklm",
                            base_dir=base_dir,
                        )
                    )
        elif ext == ".md":
            text = f.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                src = "notebooklm" if "notebooklm" in str(f).lower() else "markdown_import"
                chat_id = f"{src}_{hashlib.sha1(str(f).encode()).hexdigest()[:12]}"
                first_line = text.splitlines()[0].lstrip("# ").strip() or f.stem
                imported_chats.append(
                    upsert_chat(
                        chat_id=chat_id,
                        title=first_line[:80],
                        messages=[{"role": "assistant", "content": text}],
                        source=src,
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


async def _sync_gemini_app_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
    await asyncio.sleep(2.0)

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
                    if (scroller) scroller.scrollTop = 0;
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
                    source="browser_gemini",
                    url=url,
                    base_dir=base_dir,
                )
                synced_results.append(saved)
        except Exception as exc:
            synced_results.append({"chat_id": cid, "error": str(exc)})
    return synced_results


async def _sync_search_ai_mode_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes Google Search AI Mode (google.com/search?udm=50 / AI Mode history sidebar)."""
    await page.goto("https://www.google.com/search?udm=50", wait_until="domcontentloaded")
    await asyncio.sleep(2.5)

    await page.evaluate(
        """() => {
            const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            const histBtn = nodes.find(
                el => /history|past searches|recent/i.test(
                    (el.getAttribute('aria-label') || el.innerText || '').trim()
                )
            );
            if (histBtn) histBtn.click();
        }"""
    )
    await asyncio.sleep(2.0)

    threads = await page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('a[href*="udm=50"], a[href*="aimode"]').forEach(a => {
                const href = a.href || '';
                const rawTitle = (a.innerText || a.getAttribute('aria-label') || '').trim();
                const title = rawTitle.split('\\n')[0];
                if (href && title && title.length > 2) {
                    out.push({ url: href, title });
                }
            });
            return out;
        }"""
    )

    seen_urls: set[str] = set()
    unique_threads = []
    for t in threads:
        if t["url"] not in seen_urls:
            seen_urls.add(t["url"])
            unique_threads.append(t)

    synced: list[dict[str, Any]] = []
    for t in unique_threads[:max_chats]:
        try:
            await page.goto(t["url"], wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2.5)
            turns = await page.evaluate(
                """() => {
                    const messages = [];
                    const mainEl = document.querySelector('main') || document.body;
                    const text = (mainEl.innerText || '').trim();
                    if (text) {
                        messages.push({ role: 'assistant', content: text });
                    }
                    return messages;
                }"""
            )
            if turns:
                cid = f"aimode_{hashlib.sha1(t['url'].encode()).hexdigest()[:14]}"
                synced.append(
                    upsert_chat(
                        chat_id=cid,
                        title=t["title"][:80],
                        messages=[{"role": "user", "content": t["title"]}, *turns],
                        source="browser_search_ai_mode",
                        url=t["url"],
                        base_dir=base_dir,
                    )
                )
        except Exception as exc:
            synced.append({"url": t["url"], "error": str(exc)})
    return synced


async def _sync_aistudio_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes Google AI Studio saved prompts library (aistudio.google.com/library)."""
    await page.goto("https://aistudio.google.com/library", wait_until="domcontentloaded")
    await asyncio.sleep(3.0)

    links = await page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('a[href*="/prompts/"]').forEach(a => {
                const href = a.href || '';
                const m = href.match(/\\/prompts\\/([a-zA-Z0-9_-]{8,})/);
                const title = (a.innerText || '').trim().split('\\n')[0];
                if (m && !href.endsWith('/new_chat')) {
                    out.push({ id: m[1], url: href, title: title || `AI Studio ${m[1]}` });
                }
            });
            return out;
        }"""
    )

    seen_ids: set[str] = set()
    unique_links = []
    for item in links:
        if item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            unique_links.append(item)

    synced: list[dict[str, Any]] = []
    for item in unique_links[:max_chats]:
        try:
            await page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3.0)
            turns = await page.evaluate(
                """() => {
                    const messages = [];
                    document.querySelectorAll(
                        'ms-chat-turn, .chat-turn, [data-turn-role]'
                    ).forEach(el => {
                        const roleAttr = (
                            el.getAttribute('data-turn-role') || el.className || ''
                        ).toLowerCase();
                        const role = roleAttr.includes('user') ? 'user' : 'assistant';
                        const txt = (el.innerText || '').trim();
                        if (txt) messages.push({ role, content: txt });
                    });
                    return messages;
                }"""
            )
            if turns:
                synced.append(
                    upsert_chat(
                        chat_id=f"aistudio_{item['id']}",
                        title=item["title"][:80],
                        messages=turns,
                        source="browser_aistudio",
                        url=item["url"],
                        base_dir=base_dir,
                    )
                )
        except Exception as exc:
            synced.append({"chat_id": item["id"], "error": str(exc)})
    return synced


async def _sync_notebooklm_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes NotebookLM notebooks and chat notes (notebooklm.google.com)."""
    await page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
    await asyncio.sleep(3.0)

    notebooks = await page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('a[href*="/notebook/"]').forEach(a => {
                const href = a.href || '';
                const m = href.match(/\\/notebook\\/([a-zA-Z0-9_-]{6,})/);
                const title = (a.innerText || '').trim().split('\\n')[0];
                if (m) {
                    out.push({ id: m[1], url: href, title: title || `NotebookLM ${m[1]}` });
                }
            });
            return out;
        }"""
    )

    seen_ids: set[str] = set()
    unique_nbs = []
    for nb in notebooks:
        if nb["id"] not in seen_ids:
            seen_ids.add(nb["id"])
            unique_nbs.append(nb)

    synced: list[dict[str, Any]] = []
    for nb in unique_nbs[:max_chats]:
        try:
            await page.goto(nb["url"], wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3.0)
            content = await page.evaluate(
                """() => {
                    const mainEl = document.querySelector('main') || document.body;
                    return (mainEl.innerText || '').trim();
                }"""
            )
            if content:
                synced.append(
                    upsert_chat(
                        chat_id=f"notebooklm_{nb['id']}",
                        title=nb["title"][:80],
                        messages=[{"role": "assistant", "content": content}],
                        source="browser_notebooklm",
                        url=nb["url"],
                        base_dir=base_dir,
                    )
                )
        except Exception as exc:
            synced.append({"chat_id": nb["id"], "error": str(exc)})
    return synced


async def _sync_myactivity_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes Google's central My Activity hub (myactivity.google.com/product/gemini & search)."""
    urls = [
        ("https://myactivity.google.com/product/gemini", "myactivity_gemini"),
        ("https://myactivity.google.com/product/search", "myactivity_search_ai"),
    ]
    synced: list[dict[str, Any]] = []
    for url, src_tag in urls:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2.5)
            cards = await page.evaluate(
                """(maxItems) => {
                    const out = [];
                    const els = document.querySelectorAll(
                        'c-wiz [role="listitem"], [data-date] [ role="region" ], article'
                    );
                    els.forEach(el => {
                        if (out.length >= maxItems) return;
                        const txt = (el.innerText || '').trim();
                        if (txt && /prompted|asked|ai mode|gemini/i.test(txt)) {
                            const lines = txt.split('\\n').map(l => l.trim()).filter(Boolean);
                            out.push({
                                title: lines[0] || 'My Activity AI Entry',
                                body: lines.slice(1).join('\\n')
                            });
                        }
                    });
                    return out;
                }""",
                max_chats,
            )
            for card in cards[:max_chats]:
                title_clean = re.sub(
                    r"^(Prompted|Asked in AI Mode|Asked)\s*:?\s*",
                    "",
                    card["title"],
                    flags=re.IGNORECASE,
                ).strip()
                cid_hash = hashlib.sha1(
                    f"{src_tag}:{title_clean}:{card['body'][:80]}".encode()
                ).hexdigest()[:14]
                synced.append(
                    upsert_chat(
                        chat_id=f"{src_tag}_{cid_hash}",
                        title=title_clean[:80] or "Google AI Activity",
                        messages=[
                            {"role": "user", "content": title_clean or card["title"]},
                            {"role": "assistant", "content": card["body"] or "(Activity entry)"},
                        ],
                        source=src_tag,
                        url=url,
                        base_dir=base_dir,
                    )
                )
        except Exception as exc:
            synced.append({"url": url, "error": str(exc)})
    return synced


def _launch_chrome_visible(chrome_exe: str, profile_dir: Path, start_url: str) -> None:
    cmd = [
        chrome_exe,
        "--remote-debugging-port=9222",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        start_url,
    ]
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1  # SW_SHOWNORMAL
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, startupinfo=si, creationflags=creationflags)
    else:
        subprocess.Popen(cmd)


async def sync_from_browser(
    max_chats: int = 50,
    login_timeout_sec: int = 180,
    cdp_url: str = "http://127.0.0.1:9222",
    target: str = "gemini",
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Connects to Chrome over CDP and syncs across Google AI surfaces."""
    from playwright.async_api import async_playwright

    if target not in SUPPORTED_TARGETS:
        return {"error": f"Unsupported target '{target}'. Choose from {SUPPORTED_TARGETS}."}

    paths = ensure_dirs(base_dir)

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception:
            chrome_exe = find_chrome_executable()
            if not chrome_exe:
                return {"error": "Could not find Chrome or Edge executable on this machine."}
            _launch_chrome_visible(chrome_exe, paths["profile"], "https://gemini.google.com/app")
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
        page = context.pages[0] if context.pages else await context.new_page()
        if "google.com" not in page.url:
            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")

        deadline = time.time() + login_timeout_sec
        logged_in = False
        while time.time() < deadline:
            url = page.url
            if "accounts.google.com" not in url and "google.com" in url:
                has_chat_ui = await page.evaluate(
                    """() => {
                        const signInBtn = Array.from(document.querySelectorAll('a, button')).find(
                            el => /^sign in$/i.test((el.innerText || '').trim())
                        );
                        const hasInputOrSidebar = !!(
                            document.querySelector(
                                'rich-textarea, textarea, [contenteditable="true"], ' +
                                'conversations-list, [data-test-id*="conversation"], main'
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
                    "and run sync_google_ai_chats_from_browser again."
                ),
            }

        synced_results: list[dict[str, Any]] = []
        if target in ("gemini", "all"):
            synced_results.extend(await _sync_gemini_app_page(page, max_chats, base_dir))
        if target in ("aimode", "all"):
            synced_results.extend(await _sync_search_ai_mode_page(page, max_chats, base_dir))
        if target in ("aistudio", "all"):
            synced_results.extend(await _sync_aistudio_page(page, max_chats, base_dir))
        if target in ("notebooklm", "all"):
            synced_results.extend(await _sync_notebooklm_page(page, max_chats, base_dir))
        if target in ("myactivity", "all"):
            synced_results.extend(await _sync_myactivity_page(page, max_chats, base_dir))

        return {
            "status": "ok",
            "target": target,
            "synced_count": len([r for r in synced_results if "markdown_path" in r]),
            "synced_chats": synced_results,
        }


def _detect_url_surface_and_id(url: str) -> tuple[str, str]:
    """Classifies a Google AI URL into (source_tag, chat_id)."""
    u = url.strip()
    m_share = re.search(r"(?:gemini\.google\.com|g\.co/gemini)/share/([a-zA-Z0-9_-]+)", u)
    if m_share:
        return ("url_gemini_share", f"gemini_share_{m_share.group(1)}")

    m_app = re.search(r"gemini\.google\.com/(?:u/\d+/)?app/([a-zA-Z0-9_-]+)", u)
    if m_app:
        return ("browser_gemini", m_app.group(1))

    m_ais = re.search(r"aistudio\.google\.com/(?:app/)?prompts/([a-zA-Z0-9_-]+)", u)
    if m_ais:
        return ("browser_aistudio", f"aistudio_{m_ais.group(1)}")

    m_nb = re.search(r"notebooklm\.google\.com/notebook/([a-zA-Z0-9_-]+)", u)
    if m_nb:
        return ("browser_notebooklm", f"notebooklm_{m_nb.group(1)}")

    if "udm=50" in u or "aimode" in u:
        cid = hashlib.sha1(u.encode()).hexdigest()[:14]
        return ("browser_search_ai_mode", f"aimode_{cid}")

    cid = hashlib.sha1(u.encode()).hexdigest()[:14]
    return ("url_google_ai", f"google_ai_{cid}")


async def fetch_chat_from_url(
    url: str,
    login_timeout_sec: int = 180,
    cdp_url: str = "http://127.0.0.1:9222",
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Navigates to a Google AI chat URL, saves it locally, and returns all turns."""
    from playwright.async_api import async_playwright

    from google_ai_history.db import get_chat

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        return {"error": f"Invalid URL '{url}'. Must start with http:// or https://."}

    source_tag, chat_id = _detect_url_surface_and_id(clean_url)
    is_public_share = source_tag == "url_gemini_share"
    paths = ensure_dirs(base_dir)

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception:
            chrome_exe = find_chrome_executable()
            if not chrome_exe:
                return {"error": "Could not find Chrome or Edge executable on this machine."}
            _launch_chrome_visible(chrome_exe, paths["profile"], clean_url)
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
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass
        await page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2.5)

        if not is_public_share:
            deadline = time.time() + login_timeout_sec
            logged_in = False
            clicked_sign_in = False
            while time.time() < deadline:
                cur_url = page.url
                if "accounts.google.com" not in cur_url:
                    has_ui = await page.evaluate(
                        """(autoClick) => {
                            const btns = Array.from(document.querySelectorAll('a, button'));
                            const signInBtn = btns.find(
                                el => /^sign in$/i.test((el.innerText || '').trim())
                            );
                            if (signInBtn && autoClick) {
                                signInBtn.click();
                                return false;
                            }
                            return !signInBtn && !!document.body;
                        }""",
                        not clicked_sign_in,
                    )
                    if not has_ui:
                        clicked_sign_in = True
                    else:
                        logged_in = True
                        break
                await asyncio.sleep(2.0)

            if not logged_in:
                return {
                    "status": "login_required",
                    "message": (
                        "Please sign into your Google account in the opened Chrome window "
                        "and run fetch_google_ai_chat_url again."
                    ),
                }

            # Re-navigate to clean_url if login flow redirected to generic /app home page
            if page.url.rstrip("/") != clean_url.rstrip("/"):
                await page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)

        # Wait up to 12s for SPA conversation turns to render
        for _ in range(12):
            has_turns = await page.evaluate(
                """() => {
                    return document.querySelectorAll(
                        'user-query, model-response, [data-message-author-role], ' +
                        'ms-chat-turn, .chat-turn, [data-turn-role]'
                    ).length > 0;
                }"""
            )
            if has_turns:
                break
            await asyncio.sleep(1.0)

        # Scroll up to ensure lazy-loaded earlier turns are rendered
        await page.evaluate(
            """() => {
                const scroller = document.querySelector('infinite-scroller, main, .chat-history');
                if (scroller) scroller.scrollTop = 0;
            }"""
        )
        await asyncio.sleep(1.5)

        extracted = await page.evaluate(
            """() => {
                const messages = [];
                // 1. Gemini App / Share turns
                const geminiNodes = document.querySelectorAll(
                    'user-query, model-response, [data-message-author-role]'
                );
                if (geminiNodes.length > 0) {
                    geminiNodes.forEach(node => {
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

                // 2. AI Studio turns
                if (messages.length === 0) {
                    const studioTurns = document.querySelectorAll(
                        'ms-chat-turn, .chat-turn, [data-turn-role]'
                    );
                    studioTurns.forEach(el => {
                        const roleAttr = (
                            el.getAttribute('data-turn-role') || el.className || ''
                        ).toLowerCase();
                        const role = roleAttr.includes('user') ? 'user' : 'assistant';
                        const txt = (el.innerText || '').trim();
                        if (txt) messages.push({ role, content: txt });
                    });
                }

                // 3. Fallback for Search AI Mode, NotebookLM, or generic Google AI page
                if (messages.length === 0) {
                    const mainEl = document.querySelector('main') || document.body;
                    const txt = (mainEl?.innerText || '').trim();
                    if (txt) messages.push({ role: 'assistant', content: txt });
                }

                const titleEl = document.querySelector(
                    '[data-test-id="conversation-title"], .conversation-title, h1'
                );
                const pageTitle = (titleEl?.innerText || document.title || 'Google AI Session')
                    .replace(/^Gemini\\s*[-–—]\\s*/i, '')
                    .trim();
                return { pageTitle, messages };
            }"""
        )

        msgs = extracted.get("messages") or []
        title = extracted.get("pageTitle") or f"Google AI Chat {chat_id}"
        if not msgs:
            return {"error": f"No conversation content could be extracted from {clean_url}"}

        upsert_chat(
            chat_id=chat_id,
            title=title[:100],
            messages=msgs,
            source=source_tag,
            url=clean_url,
            base_dir=base_dir,
        )
        full_record = get_chat(chat_id, base_dir=base_dir)
        return {
            "status": "ok",
            "chat": full_record,
        }
