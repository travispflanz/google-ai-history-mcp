import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
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


def _log_progress(message: str) -> None:
    """Writes live progress updates to stderr so stdout remains clean JSON for MCP."""
    print(f"[google-ai-history] {message}", file=sys.stderr, flush=True)


def _strip_directional_chars(text: str) -> str:
    """Strips invisible Unicode directional marks (e.g. LRM \\u200e) that break cp1252 consoles."""
    if not text:
        return ""
    return re.sub(r"[\u200b-\u200f\ufeff]", "", text).strip()


def _clean_gemini_title(raw_title: str) -> str:
    """Normalizes a Gemini page or sidebar title, stripping Google Gemini prefixes/suffixes."""
    title = _strip_directional_chars(raw_title)
    title = re.sub(r"^Gemini\s*[-–—]\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[-–—]\s*(?:Google\s+)?Gemini$", "", title, flags=re.IGNORECASE)
    title = title.strip()
    if re.fullmatch(r"(?:Google\s+Gemini|Conversation\s+with\s+Gemini|Gemini)", title, re.I):
        return ""
    return title


def _canonicalize_aimode_url(
    url: str,
    thread_id: str | None = None,
    query: str | None = None,
) -> str:
    """Returns a stable Search AI Mode URL without ephemeral sxsrf/ved/mstk session params."""
    u = _normalize_google_ai_url(url)
    parsed = urllib.parse.urlsplit(u)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)

    mtid = (qs.get("mtid") or [""])[0] or (thread_id or "")
    if not mtid and parsed.fragment:
        m_frag = re.search(r"thread=([a-zA-Z0-9_-]+)", parsed.fragment)
        if m_frag:
            mtid = m_frag.group(1)
    q_val = (qs.get("q") or [""])[0] or (query or "")

    params: list[tuple[str, str]] = [("udm", "50")]
    if mtid:
        params.append(("mtid", mtid))
    if q_val:
        params.append(("q", q_val))
    return f"https://www.google.com/search?{urllib.parse.urlencode(params)}"


def _parse_surface_timestamp(
    raw: str | None,
    data_date: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Parses surface timestamps (ISO, data-date + time, 'Oct 26, 1:19 PM', relative) into UTC."""
    ref_local = (now or datetime.now().astimezone()).astimezone()
    text = _strip_directional_chars(raw or "")
    text = re.sub(r"\s+", " ", text).strip()

    if text and re.match(r"^\d{4}-\d{2}-\d{2}[T\s]", text):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            pass

    # Relative times from AI Studio Library ("9 months ago", "2 days ago")
    m_rel = re.match(
        r"^(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$",
        text,
        flags=re.IGNORECASE,
    )
    if m_rel:
        qty = int(m_rel.group(1))
        unit = m_rel.group(2).lower()
        unit_days = {
            "second": 0,
            "minute": 0,
            "hour": 0,
            "day": 1,
            "week": 7,
            "month": 30,
            "year": 365,
        }
        if unit == "second":
            dt = ref_local - timedelta(seconds=qty)
        elif unit == "minute":
            dt = ref_local - timedelta(minutes=qty)
        elif unit == "hour":
            dt = ref_local - timedelta(hours=qty)
        else:
            dt = ref_local - timedelta(days=qty * unit_days[unit])
        return dt.astimezone(UTC).isoformat()

    # Extract optional time-of-day like "1:19 PM" or "11:02 AM"
    m_time = re.search(r"(\d{1,2}):(\d{2})(?:\s*([AP]M))?", text, flags=re.IGNORECASE)
    hour, minute = 0, 0
    has_time = bool(m_time)
    if m_time:
        hour = int(m_time.group(1))
        minute = int(m_time.group(2))
        ampm = (m_time.group(3) or "").upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0

    if data_date and re.fullmatch(r"\d{8}", data_date.strip()):
        try:
            base = datetime.strptime(data_date.strip(), "%Y%m%d")
            dt = base.replace(hour=hour, minute=minute, tzinfo=ref_local.tzinfo)
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            pass

    if re.search(r"\b(Today|Yesterday)\b", text, flags=re.IGNORECASE):
        day_offset = -1 if re.search(r"\bYesterday\b", text, flags=re.IGNORECASE) else 0
        base_date = (ref_local + timedelta(days=day_offset)).date()
        dt = datetime(
            base_date.year,
            base_date.month,
            base_date.day,
            hour,
            minute,
            tzinfo=ref_local.tzinfo,
        )
        return dt.astimezone(UTC).isoformat()

    # Month + day + optional year ("Oct 26, 1:19 PM", "November 3 at 11:02 AM", "Nov 3, 2025")
    m_md = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+(\d{1,2})(?:,?\s*(\d{4}))?",
        text,
        flags=re.IGNORECASE,
    )
    if m_md:
        mon_str = m_md.group(1)[:3].title()
        day_num = int(m_md.group(2))
        year_num = int(m_md.group(3)) if m_md.group(3) else ref_local.year
        try:
            parsed_month = datetime.strptime(mon_str, "%b").month
            dt = datetime(
                year_num,
                parsed_month,
                day_num,
                hour if has_time else 0,
                minute if has_time else 0,
                tzinfo=ref_local.tzinfo,
            )
            if not m_md.group(3) and dt > ref_local + timedelta(days=1):
                dt = dt.replace(year=year_num - 1)
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            pass

    return None


def _normalize_google_ai_url(url: str) -> str:
    """Prepends https:// to bare Google AI URLs like 'share.gemini.google/...'."""
    u = url.strip()
    if re.match(
        r"^(?:share\.gemini\.google|gemini\.google\.com|g\.co/gemini|"
        r"aistudio\.google\.com|notebooklm\.google\.com|myactivity\.google\.com|"
        r"(?:www\.)?google\.com)/",
        u,
        flags=re.IGNORECASE,
    ):
        return f"https://{u}"
    return u


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


_GEMINI_EXTRACT_JS = """() => {
    const messages = [];
    const turnNodes = document.querySelectorAll(
        'user-query, model-response, share-turn-viewer response-container, ' +
        '[data-message-author-role], .conversation-container'
    );
    if (turnNodes.length > 0) {
        turnNodes.forEach(node => {
            const tag = node.tagName.toLowerCase();
            const roleAttr = node.getAttribute('data-message-author-role');
            if (tag === 'user-query' || roleAttr === 'user') {
                const qEl = node.querySelector('.query-text, .user-query-text') || node;
                const txt = (qEl.innerText || '')
                    .replace(/^You said[^\\n]*\\n+/i, '')
                    .replace(/^You said\\s*/i, '')
                    .trim();
                if (txt) messages.push({ role: 'user', content: txt });
            } else if (
                tag === 'model-response' ||
                tag === 'response-container' ||
                roleAttr === 'model'
            ) {
                const md = node.querySelector(
                    'message-content, .markdown, .model-response-text'
                ) || node;
                const txt = (md.innerText || '')
                    .replace(/^Gemini said\\s*/i, '')
                    .trim();
                if (txt) messages.push({ role: 'assistant', content: txt });
            }
        });
    }
    const titleEl = document.querySelector(
        '[data-test-id="conversation-title"], .conversation-title:not(.picker-secondary-text)'
    );
    const rawDocTitle = (document.title || '').trim();
    const rawElTitle = (titleEl?.innerText || '').trim();
    const pageTitle = rawDocTitle || rawElTitle;
    const hasDeepResearchCard = !!(
        document.querySelector(
            'deep-research-immersive-panel, immersive-panel, immersive-entry-chip, ' +
            '[data-test-id*="immersive" i], [data-test-id*="research" i]'
        ) ||
        messages.some(m =>
            m.role === 'assistant' &&
            /I've completed your research\\./i.test(m.content || '')
        )
    );
    return { pageTitle, titleElText: rawElTitle, messages, hasDeepResearchCard };
}"""


def _extract_gemini_card_timestamp(messages: list[dict[str, Any]]) -> str | None:
    """Finds a date/time stamp like 'Oct 26, 1:19 PM' inside a Deep Research completion turn."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        lines = [ln.strip() for ln in str(msg.get("content") or "").splitlines() if ln.strip()]
        for ln in reversed(lines[-3:]):
            ts = _parse_surface_timestamp(ln)
            if ts:
                return ts
    return None


async def _extract_gemini_deep_research_report(page: Any) -> dict[str, Any]:
    """Opens and scrapes Gemini's Deep Research immersive panel if present."""
    panel_js = """() => {
        const panel = document.querySelector(
            'deep-research-immersive-panel, immersive-panel'
        );
        if (!panel) return null;
        const container = panel.querySelector(
            'message-content, .markdown, structured-content-container, response-element'
        ) || panel;
        const clone = container.cloneNode(true);
        clone.querySelectorAll(
            'button, mat-icon, .material-symbols-outlined, .material-icons, [aria-hidden="true"]'
        ).forEach(el => el.remove());
        const text = (clone.innerText || clone.textContent || '').trim();
        return { text, length: text.length };
    }"""

    try:
        panel_res = await page.evaluate(panel_js)
        if isinstance(panel_res, dict) and (panel_res.get("text") or "").strip():
            return {
                "found_card": True,
                "report": _strip_directional_chars(panel_res["text"]),
            }

        clicked = await page.evaluate(
            """() => {
                const chips = document.querySelectorAll(
                    'immersive-entry-chip button, immersive-entry-chip [role="button"], ' +
                    '[data-test-id*="immersive" i] button, model-response immersive-entry-chip'
                );
                for (const c of chips) {
                    c.click();
                    return true;
                }
                const btns = Array.from(
                    document.querySelectorAll(
                        'model-response button, model-response [role="button"]'
                    )
                );
                const skipRe = new RegExp(
                    '^(Copy|Edit|Good response|Bad response|' +
                    'Share & export|Show more options|Edit plan|Start research)$',
                    'i'
                );
                for (const b of btns) {
                    const label = (b.getAttribute('aria-label') || b.innerText || '').trim();
                    if (!label || skipRe.test(label)) continue;
                    if (
                        /research|open|view report/i.test(label) ||
                        b.closest('immersive-entry-chip')
                    ) {
                        b.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        if clicked:
            for _ in range(8):
                await asyncio.sleep(0.5)
                panel_res = await page.evaluate(panel_js)
                if isinstance(panel_res, dict) and (panel_res.get("text") or "").strip():
                    return {
                        "found_card": True,
                        "report": _strip_directional_chars(panel_res["text"]),
                    }
            return {"found_card": True, "report": None}
    except Exception:
        pass
    return {"found_card": False, "report": None}


async def _extract_gemini_chat_from_page(
    page: Any,
    title_hint: str = "",
) -> dict[str, Any]:
    """Extracts turns, Deep Research report body, timestamps, and canonical title from Gemini."""
    harvested = await _harvest_gemini_turns_from_page(page)
    extracted = await page.evaluate(_GEMINI_EXTRACT_JS) or {}
    if not isinstance(extracted, dict):
        extracted = {}

    msgs: list[dict[str, Any]] = (
        list(harvested) if harvested else list(extracted.get("messages") or [])
    )

    dr_ts = _extract_gemini_card_timestamp(msgs)
    if dr_ts and msgs:
        for m in reversed(msgs):
            if m.get("role") == "assistant" and not m.get("timestamp"):
                m["timestamp"] = dr_ts
                break

    dr_info = await _extract_gemini_deep_research_report(page)
    incomplete: str | None = None
    has_dr_card = bool(extracted.get("hasDeepResearchCard") or dr_info.get("found_card"))
    report_text = dr_info.get("report")
    if report_text:
        already_captured = any(report_text[:120] in str(m.get("content") or "") for m in msgs)
        if not already_captured:
            report_msg: dict[str, Any] = {
                "role": "assistant",
                "kind": "deep_research_report",
                "content": report_text,
            }
            if dr_ts:
                report_msg["timestamp"] = dr_ts
            msgs.append(report_msg)
    elif has_dr_card:
        incomplete = "deep_research_report_not_captured"

    cand_titles = [
        _clean_gemini_title(str(extracted.get("pageTitle") or "")),
        _clean_gemini_title(str(extracted.get("titleElText") or "")),
        _clean_gemini_title(title_hint),
    ]
    final_title = next((t for t in cand_titles if t), "")
    if not final_title and msgs:
        final_title = _strip_directional_chars(str(msgs[0].get("content") or "")).splitlines()[0][
            :80
        ]

    return {
        "title": final_title or "Google Gemini Chat",
        "messages": msgs,
        "incomplete": incomplete,
        "created_at": dr_ts,
    }


async def _sync_gemini_app_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
    extra_chats: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    _log_progress(f"Starting Gemini sync (max_chats={max_chats})...")
    collected_chats: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    if extra_chats is None:
        await page.goto("https://gemini.google.com/search", wait_until="domcontentloaded")
        await asyncio.sleep(4.0)

        await page.evaluate(
            """() => {
                const sidenav = document.querySelector('bard-sidenav');
                const isCollapsed = !!(sidenav && sidenav.className.includes('collapsed'));
                const isOpen = !isCollapsed && !!document.querySelector(
                    'bard-sidenav.sidenav-open, button[aria-label*="Close sidebar" i], ' +
                    'conversations-list a[href*="/app/"]'
                );
                if (!isOpen || isCollapsed) {
                    const menuBtn = document.querySelector(
                        'button[data-test-id="side-nav-sparkle-button"], ' +
                        'button[aria-label*="Open sidebar" i], ' +
                        'button[data-test-id="side-nav-menu-button"], ' +
                        'button[aria-label*="Main menu" i], ' +
                        'button[aria-label*="Open" i]'
                    );
                    if (menuBtn) menuBtn.click();
                }
            }"""
        )
        await asyncio.sleep(2.0)

        stale_scroll_rounds = 0
        for attempt in range(50):
            items = await page.evaluate(
                """() => {
                    const out = [];
                    document.querySelectorAll('a[href*="/app/"]').forEach(a => {
                        const href = a.getAttribute('href') || '';
                        if (/accounts\\.google\\.com|SignOutOptions/i.test(href)) return;
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
                    const moreBtn = Array.from(
                        document.querySelectorAll('conversations-list button, bard-sidenav button')
                    ).find(el => /show more|load more/i.test((el.innerText || '').trim()));
                    if (moreBtn) {
                        moreBtn.click();
                        return true;
                    }
                    const scrollers = document.querySelectorAll(
                        'bard-sidenav infinite-scroller, ' +
                        'side-navigation-content infinite-scroller, ' +
                        'conversations-list, infinite-scroller:not(.chat-history), ' +
                        'nav, [role="navigation"]'
                    );
                    let didScroll = false;
                    scrollers.forEach(s => {
                        if (
                            s.scrollHeight > s.clientHeight &&
                            s.scrollTop + s.clientHeight < s.scrollHeight - 5
                        ) {
                            s.scrollTop = s.scrollHeight;
                            didScroll = true;
                        }
                    });
                    return didScroll;
                }"""
            )
            await asyncio.sleep(1.5)
            if not scrolled and new_found == 0:
                stale_scroll_rounds += 1
                if (len(collected_chats) > 0 and stale_scroll_rounds >= 2) or attempt >= 4:
                    break
            else:
                stale_scroll_rounds = 0
    else:
        for item in extra_chats:
            cid = item["id"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                collected_chats.append(item)

    _log_progress(f"[gemini] Found {len(collected_chats)} chat(s); syncing up to {max_chats}...")
    synced_results: list[dict[str, Any]] = []
    targets = collected_chats[:max_chats]
    for idx, chat_meta in enumerate(targets, start=1):
        cid = chat_meta["id"]
        url = chat_meta["url"]
        title_hint = chat_meta.get("title") or f"Gemini Chat {cid}"
        _log_progress(f"[gemini] ({idx}/{len(targets)}) {cid}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(4.0)
            parsed = await _extract_gemini_chat_from_page(page, title_hint=title_hint)
            if parsed["messages"]:
                saved = upsert_chat(
                    chat_id=cid,
                    title=parsed["title"],
                    messages=parsed["messages"],
                    source="browser_gemini",
                    url=url,
                    created_at=parsed.get("created_at"),
                    base_dir=base_dir,
                    incomplete=parsed.get("incomplete"),
                )
                synced_results.append(saved)
        except Exception as exc:
            synced_results.append({"chat_id": cid, "error": str(exc)})
    _log_progress(
        f"[gemini] Completed: {len([r for r in synced_results if 'markdown_path' in r])} synced."
    )
    return synced_results


async def _harvest_gemini_turns_from_page(page: Any) -> list[dict[str, Any]]:
    """Climbs infinite-scroller.chat-history to top and walks down merging virtualised turns."""
    harvest_js = """() => {
        const nodes = Array.from(document.querySelectorAll('user-query, model-response'));
        const out = [];
        for (const node of nodes) {
            const tag = node.tagName.toLowerCase();
            if (tag === 'user-query') {
                const el = node.querySelector('.query-text, .user-query-text') || node;
                let t = (el.innerText || '').trim();
                t = t.replace(/^You said[^\\n]*\\n+/i, '')
                     .replace(/^You said\\s*/i, '').trim();
                if (t) out.push({ role: 'user', content: t });
            } else {
                const el = node.querySelector(
                    'message-content, .markdown, .model-response-text'
                ) || node;
                const t = (el.innerText || '').trim()
                    .replace(/^Gemini said\\s*/i, '').trim();
                if (t) out.push({ role: 'assistant', content: t });
            }
        }
        return out;
    }"""

    async def _scroll_chat(frac: float) -> dict[str, Any]:
        res = await page.evaluate(
            """(frac) => {
                const el = document.querySelector('infinite-scroller.chat-history')
                    || document.querySelector('infinite-scroller');
                if (!el) return { top: 0, height: 0, client: 0 };
                el.scrollTop = Math.max(0, el.scrollTop + el.clientHeight * frac);
                return { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight };
            }""",
            frac,
        )
        return res if isinstance(res, dict) else {}

    harvested: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    def _absorb(window: list[dict[str, Any]] | None) -> None:
        for w in window or []:
            if not isinstance(w, dict):
                continue
            role = str(w.get("role") or "")
            content = _strip_directional_chars(str(w.get("content") or ""))
            if not role or not content:
                continue
            k = (role, content)
            if k not in seen_keys:
                seen_keys.add(k)
                harvested.append({"role": role, "content": content})

    try:
        # 1) Climb to the very top so the earliest turns mount first
        for _ in range(40):
            info = await _scroll_chat(-1.0)
            await asyncio.sleep(0.6)
            if info.get("top", 1) <= 0:
                break
        await asyncio.sleep(0.5)

        # 2) Walk downward, merging each rendered window in chronological order
        prev_top: Any = -1
        for _ in range(80):
            _absorb(await page.evaluate(harvest_js))
            info = await _scroll_chat(0.75)
            await asyncio.sleep(0.6)
            at_bottom = info.get("top", 0) + info.get("client", 0) >= info.get("height", 1)
            if at_bottom:
                _absorb(await page.evaluate(harvest_js))
                break
            if info.get("top") == prev_top:
                break
            prev_top = info.get("top")
    except Exception:
        pass
    return harvested


async def _sync_search_ai_mode_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes Google Search AI Mode (google.com/search?udm=50 / AI Mode history sidebar)."""
    _log_progress(f"Starting Search AI Mode sync (max_chats={max_chats})...")
    await page.goto("https://www.google.com/search?udm=50", wait_until="domcontentloaded")
    await asyncio.sleep(3.0)

    await page.evaluate(
        """() => {
            const openBtn = document.querySelector(
                'button[aria-label="Open sidebar"], a[aria-label="AI Mode history"]'
            );
            if (openBtn && !document.querySelector('button[data-thread-id]')) {
                openBtn.click();
                return;
            }
            const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            const histBtn = nodes.find(
                el => /history|past searches|recent/i.test(
                    (el.getAttribute('aria-label') || el.innerText || '').trim()
                )
            );
            if (histBtn) histBtn.click();
        }"""
    )
    await asyncio.sleep(2.5)

    threads = await page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('button[data-thread-id]').forEach(btn => {
                const tid = btn.getAttribute('data-thread-id') || '';
                const title = (btn.innerText || '').trim().split('\\n')[0];
                if (tid && title) {
                    out.push({
                        url: `https://www.google.com/search?udm=50#thread=${tid}`,
                        title,
                        threadId: tid
                    });
                }
            });
            document.querySelectorAll('a[href*="udm=50"], a[href*="aimode"]').forEach(a => {
                const href = a.href || '';
                if (/maps\\.google\\.com/i.test(href)) return;
                const rawTitle = (a.innerText || a.getAttribute('aria-label') || '').trim();
                const title = rawTitle.split('\\n')[0];
                const navTabs = /^(Maps|All|Images|Videos)$/i;
                if (href && title && title.length > 2 && !navTabs.test(title)) {
                    out.push({ url: href, title });
                }
            });
            return out;
        }"""
    )

    seen_urls: set[str] = set()
    unique_threads = []
    for t in threads:
        canon = _canonicalize_aimode_url(
            t["url"], thread_id=t.get("threadId"), query=t.get("title")
        )
        if canon not in seen_urls:
            seen_urls.add(canon)
            unique_threads.append(t)

    _log_progress(f"[aimode] Found {len(unique_threads)} thread(s); syncing up to {max_chats}...")
    synced: list[dict[str, Any]] = []
    targets = unique_threads[:max_chats]
    for idx, t in enumerate(targets, start=1):
        _log_progress(f"[aimode] ({idx}/{len(targets)}) {t.get('title', '')[:40]}")
        try:
            tid = t.get("threadId")
            if tid:
                await page.evaluate(
                    """(threadId) => {
                        const openBtn = document.querySelector(
                            'button[aria-label="Open sidebar"], a[aria-label="AI Mode history"]'
                        );
                        let btn = document.querySelector(`button[data-thread-id="${threadId}"]`);
                        if (!btn && openBtn) {
                            openBtn.click();
                        }
                        btn = document.querySelector(`button[data-thread-id="${threadId}"]`);
                        if (btn) btn.click();
                    }""",
                    tid,
                )
            else:
                await page.goto(t["url"], wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3.0)
            turns = await page.evaluate(
                """() => {
                    const messages = [];
                    const mainEl = document.querySelector('main, [role="main"]') || document.body;
                    let text = (mainEl.innerText || '').trim();
                    const marker = 'AI Mode Conversation:';
                    const idx = text.indexOf(marker);
                    if (idx !== -1) {
                        text = text.slice(idx).trim();
                        const pattern = new RegExp(
                            'You said:\\\\s*([^\\\\n]+)\\\\n(?:[^\\\\n]*\\\\n){0,4}?' +
                            'AI Mode reply for [^\\\\n]+\\\\n([\\\\s\\\\S]*?)' +
                            '(?=\\\\nYou said:|\\\\nSkip to previous prompt|\\\\nAI Mode reply|$)',
                            'g'
                        );
                        let match;
                        while ((match = pattern.exec(text)) !== null) {
                            const p = (match[1] || '').trim();
                            let r = (match[2] || '').trim().replace(/\\nShow all\\s*$/, '').trim();
                            if (p) messages.push({ role: 'user', content: p });
                            if (r) messages.push({ role: 'assistant', content: r });
                        }
                    }
                    if (messages.length === 0 && text) {
                        messages.push({ role: 'assistant', content: text });
                    }
                    return messages;
                }"""
            )
            if turns:
                raw_url = page.url if tid and "udm=50" in page.url else t["url"]
                first_user_q = next(
                    (m["content"] for m in turns if m.get("role") == "user" and m.get("content")),
                    None,
                )
                canonical_url = _canonicalize_aimode_url(
                    raw_url,
                    thread_id=tid,
                    query=first_user_q or t.get("title"),
                )
                cid = f"aimode_{tid or hashlib.sha1(canonical_url.encode()).hexdigest()[:14]}"
                final_messages = (
                    turns
                    if (turns and turns[0].get("role") == "user")
                    else [{"role": "user", "content": t["title"]}, *turns]
                )
                synced.append(
                    upsert_chat(
                        chat_id=cid,
                        title=t["title"][:80],
                        messages=final_messages,
                        source="browser_search_ai_mode",
                        url=canonical_url,
                        base_dir=base_dir,
                    )
                )
        except Exception as exc:
            synced.append({"url": t["url"], "error": str(exc)})
    _log_progress(f"[aimode] Completed: {len([r for r in synced if 'markdown_path' in r])} synced.")
    return synced


_AISTUDIO_HARVEST_JS = """() => {
    const out = [];
    const attachments = [];

    const stripIcons = (root) => {
        const clone = root.cloneNode(true);
        clone.querySelectorAll(
            '.actions-container, .author-label, mat-icon, ' +
            '.material-symbols-outlined, .material-symbols-rounded, .material-symbols-filled, ' +
            '.material-icons, [aria-hidden="true"], button, ms-chat-turn-options'
        ).forEach(el => el.remove());
        return clone;
    };

    const parseFileChunk = (fc) => {
        const nameEl = fc.querySelector('.name, [title]');
        const name = (nameEl?.getAttribute('title') || nameEl?.innerText || fc.innerText || '')
            .trim()
            .split('\\n')[0]
            .trim();
        const tokensEl = fc.querySelector('.token-count');
        const tokens = (tokensEl?.innerText || '').trim();
        let driveId = fc.getAttribute('data-drive-id') ||
            fc.querySelector('[data-drive-id]')?.getAttribute('data-drive-id') || '';
        let url = '';
        const linkEl = fc.querySelector('a[href*="drive.google"], a[href*="docs.google"], a[href]');
        if (linkEl) {
            url = linkEl.getAttribute('href') || '';
        } else {
            const img = fc.querySelector('img[src]');
            if (img) url = img.getAttribute('src') || '';
        }
        if (!driveId && url) {
            const m = url.match(/\\/d\\/([a-zA-Z0-9_-]{10,})/) ||
                url.match(/[?&]id=([a-zA-Z0-9_-]{10,})/);
            if (m) driveId = m[1];
        }
        if (driveId && (!url || url.includes('drive-storage'))) {
            url = `https://drive.google.com/file/d/${driveId}/view`;
        }
        if (name || driveId) {
            return {
                name: name || driveId || 'Drive Attachment',
                tokens: tokens || null,
                drive_id: driveId || null,
                url: url || null,
            };
        }
        return null;
    };

    const msTurns = Array.from(document.querySelectorAll('ms-chat-turn'));
    if (msTurns.length > 0) {
        msTurns.forEach((turn, idx) => {
            const turnId = turn.id || `idx_${idx}`;
            const roleNode = turn.querySelector('[data-turn-role]');
            const container = turn.querySelector('.chat-turn-container');
            const roleRaw = (
                roleNode?.getAttribute('data-turn-role') ||
                container?.className ||
                turn.className ||
                ''
            ).toLowerCase();
            const role = roleRaw.includes('user') ? 'user' : 'assistant';

            const contentRoot = turn.querySelector('.turn-content') || roleNode || turn;
            const turnAttachments = [];
            contentRoot.querySelectorAll('ms-file-chunk').forEach(fc => {
                const att = parseFileChunk(fc);
                if (att) {
                    turnAttachments.push(att);
                    attachments.push(att);
                }
            });

            // Thoughts
            let thoughtText = '';
            const thoughtChunk = contentRoot.querySelector('ms-thought-chunk');
            if (thoughtChunk) {
                const body = thoughtChunk.querySelector('.mat-expansion-panel-body, ms-text-chunk');
                if (body) {
                    const cleanBody = stripIcons(body);
                    thoughtText = (cleanBody.innerText || cleanBody.textContent || '').trim();
                }
            }

            // Regular body (without thoughts or file chunks or icons)
            const bodyClone = stripIcons(contentRoot);
            bodyClone.querySelectorAll(
                'ms-thought-chunk, ms-file-chunk'
            ).forEach(el => el.remove());
            const bodyText = (bodyClone.innerText || bodyClone.textContent || '').trim();

            out.push({
                id: turnId,
                role,
                text: bodyText,
                thought: thoughtText,
                attachments: turnAttachments,
            });
        });
    } else {
        // Legacy/fallback selector for non-ms-chat-turn DOMs
        document.querySelectorAll('.chat-turn, [data-turn-role]').forEach((el, idx) => {
            if (el.closest('ms-chat-turn')) return;
            const roleRaw = (
                el.getAttribute('data-turn-role') || el.className || ''
            ).toLowerCase();
            const role = roleRaw.includes('user') ? 'user' : 'assistant';
            const clean = stripIcons(el);
            const txt = (clean.innerText || clean.textContent || '').trim();
            if (txt) {
                out.push({ id: `legacy_${idx}`, role, text: txt, thought: '', attachments: [] });
            }
        });
    }

    const titleEl = document.querySelector('h1.mode-title, .page-title h1, h1');
    let pageTitle = '';
    if (titleEl) {
        const cleanTitle = stripIcons(titleEl);
        pageTitle = (cleanTitle.innerText || cleanTitle.textContent || '').trim();
    }
    if (!pageTitle) {
        pageTitle = (document.title || '').replace(/\\s*\\|\\s*Google AI Studio$/i, '').trim();
    }
    return { pageTitle, turns: out, attachments };
}"""


async def _extract_aistudio_from_page(
    page: Any,
    title_hint: str = "",
) -> dict[str, Any]:
    """Harvests virtualized AI Studio turns, thoughts, and Drive attachments cleanly."""
    collected_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    attachments_map: dict[tuple[str, str], dict[str, Any]] = {}
    extracted_title = ""

    def _absorb(res: Any) -> None:
        nonlocal extracted_title
        if isinstance(res, list):
            # Support simplified mock tests returning a list of {role, content}
            for idx, item in enumerate(res):
                if not isinstance(item, dict):
                    continue
                tid = f"mock_{idx}"
                if tid not in collected_by_id:
                    ordered_ids.append(tid)
                collected_by_id[tid] = {
                    "role": item.get("role", "user"),
                    "text": str(item.get("content") or "").strip(),
                    "thought": "",
                    "attachments": [],
                }
            return
        if not isinstance(res, dict):
            return
        if res.get("pageTitle") and not extracted_title:
            extracted_title = _strip_directional_chars(str(res["pageTitle"]))
        for att in res.get("attachments") or []:
            if isinstance(att, dict):
                key = (str(att.get("name") or ""), str(att.get("drive_id") or ""))
                attachments_map[key] = att
        for t in res.get("turns") or []:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or f"turn_{len(ordered_ids)}")
            prev = collected_by_id.get(tid)
            if tid not in collected_by_id:
                ordered_ids.append(tid)
            # Keep whichever version actually rendered non-empty content/attachments
            has_new_data = bool(
                (t.get("text") or "").strip()
                or (t.get("thought") or "").strip()
                or (t.get("attachments") or [])
            )
            if prev is None or has_new_data:
                if prev is not None and not has_new_data:
                    continue
                collected_by_id[tid] = t

    try:
        # Scroll to top so early turns (such as Turn 0 Drive attachments) mount
        await page.evaluate(
            """() => {
                document.querySelectorAll(
                    'ms-thought-chunk mat-expansion-panel-header[aria-expanded="false"]'
                ).forEach(hdr => hdr.click());
                const sc = document.querySelector('ms-autoscroll-container');
                if (sc) sc.scrollTop = 0;
            }"""
        )
        await asyncio.sleep(0.6)
        _absorb(await page.evaluate(_AISTUDIO_HARVEST_JS))

        # Walk downward if ms-autoscroll-container is tall/virtualized
        prev_top = -1
        for _ in range(40):
            scroll_info = await page.evaluate(
                """() => {
                    const sc = document.querySelector('ms-autoscroll-container');
                    if (!sc || sc.scrollHeight <= sc.clientHeight + 20) {
                        return { top: 0, height: 0, client: 0 };
                    }
                    sc.scrollTop = sc.scrollTop + Math.max(300, sc.clientHeight * 0.75);
                    document.querySelectorAll(
                        'ms-thought-chunk mat-expansion-panel-header[aria-expanded="false"]'
                    ).forEach(hdr => hdr.click());
                    return { top: sc.scrollTop, height: sc.scrollHeight, client: sc.clientHeight };
                }"""
            )
            if not isinstance(scroll_info, dict) or scroll_info.get("height", 0) == 0:
                break
            await asyncio.sleep(0.4)
            _absorb(await page.evaluate(_AISTUDIO_HARVEST_JS))
            at_bottom = (
                scroll_info.get("top", 0) + scroll_info.get("client", 0)
                >= scroll_info.get("height", 1) - 5
            )
            if at_bottom or scroll_info.get("top") == prev_top:
                break
            prev_top = scroll_info.get("top", -1)
    except Exception:
        pass

    messages: list[dict[str, Any]] = []
    for tid in ordered_ids:
        t = collected_by_id[tid]
        role = str(t.get("role") or "user")
        thought = _strip_directional_chars(str(t.get("thought") or ""))
        text = _strip_directional_chars(str(t.get("text") or ""))
        turn_atts = t.get("attachments") or []

        if thought and not re.fullmatch(
            r"(?:Thoughts\s*)?Expand to view model thoughts", thought, re.I
        ):
            messages.append(
                {
                    "role": "assistant",
                    "kind": "thought",
                    "content": f"*(Thinking)*\n{thought}",
                }
            )
        if text:
            messages.append({"role": role, "content": text})
        elif turn_atts:
            att_lines = []
            for att in turn_atts:
                label = str(att.get("name") or "Attachment")
                tok = att.get("tokens")
                did = att.get("drive_id")
                extra = ", ".join(p for p in (tok, f"Drive ID: {did}" if did else "") if p)
                att_lines.append(f"[Attachment: {label}" + (f" ({extra})]" if extra else "]"))
            messages.append({"role": role, "kind": "attachment", "content": "\n".join(att_lines)})

    return {
        "title": extracted_title or _strip_directional_chars(title_hint) or "Google AI Studio",
        "messages": messages,
        "attachments": list(attachments_map.values()),
    }


async def _sync_aistudio_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes Google AI Studio saved prompts library (aistudio.google.com/library)."""
    _log_progress(f"Starting AI Studio sync (max_chats={max_chats})...")
    await page.goto("https://aistudio.google.com/library", wait_until="domcontentloaded")
    await asyncio.sleep(3.0)

    links = await page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('a[href*="/prompts/"]').forEach(a => {
                const href = a.href || '';
                const m = href.match(/\\/prompts\\/([a-zA-Z0-9_-]{8,})/);
                const title = (a.innerText || '').trim().split('\\n')[0];
                const row = a.closest('tr, [role="row"]');
                let updatedText = '';
                if (row) {
                    const cells = Array.from(row.querySelectorAll('td, [role="cell"]'));
                    if (cells.length >= 2) {
                        updatedText = (cells[1].innerText || '').trim();
                    }
                }
                if (m && !href.endsWith('/new_chat')) {
                    out.push({
                        id: m[1],
                        url: href,
                        title: title || `AI Studio ${m[1]}`,
                        updatedText,
                    });
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

    _log_progress(f"[aistudio] Found {len(unique_links)} prompt(s); syncing up to {max_chats}...")
    synced: list[dict[str, Any]] = []
    targets = unique_links[:max_chats]
    for idx, item in enumerate(targets, start=1):
        _log_progress(f"[aistudio] ({idx}/{len(targets)}) {item['id']}")
        try:
            await page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3.0)
            parsed = await _extract_aistudio_from_page(page, title_hint=item["title"])
            parsed_ts = _parse_surface_timestamp(item.get("updatedText"))
            if parsed["messages"]:
                synced.append(
                    upsert_chat(
                        chat_id=f"aistudio_{item['id']}",
                        title=parsed["title"][:80],
                        messages=parsed["messages"],
                        source="browser_aistudio",
                        url=item["url"],
                        created_at=parsed_ts,
                        updated_at=parsed_ts,
                        base_dir=base_dir,
                        attachments=parsed.get("attachments") or None,
                    )
                )
        except Exception as exc:
            synced.append({"chat_id": item["id"], "error": str(exc)})
    _log_progress(
        f"[aistudio] Completed: {len([r for r in synced if 'markdown_path' in r])} synced."
    )
    return synced


async def _sync_notebooklm_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes NotebookLM notebooks and chat notes (notebooklm.google.com)."""
    _log_progress(f"Starting NotebookLM sync (max_chats={max_chats})...")
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

    _log_progress(f"[notebooklm] Found {len(unique_nbs)} notebook(s); syncing up to {max_chats}...")
    synced: list[dict[str, Any]] = []
    targets = unique_nbs[:max_chats]
    for idx, nb in enumerate(targets, start=1):
        _log_progress(f"[notebooklm] ({idx}/{len(targets)}) {nb['id']}")
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
    _log_progress(
        f"[notebooklm] Completed: {len([r for r in synced if 'markdown_path' in r])} synced."
    )
    return synced


def _parse_myactivity_dialog_text(
    dialog_text: str,
    links: list[str],
    fallback_prompt: str,
) -> dict[str, Any]:
    """Parses prompt, assistant response, timestamp string, and source_chat_id from a dialog."""
    lines = [_strip_directional_chars(ln) for ln in dialog_text.splitlines()]
    lines = [ln for ln in lines if ln]

    skip_headers = {
        "Item details",
        "more_vert",
        "close",
        "Gemini Apps",
        "Search",
        "AI Mode",
        "Details",
        "event",
        "apps",
        "chat",
        "search",
    }
    footer_markers = ("Why this activity?", "If you use a shared device")

    prompt_text = ""
    time_text = ""
    body_lines: list[str] = []
    seen_details = False
    for ln in lines:
        if any(ln.startswith(m) for m in footer_markers):
            break
        if ln in skip_headers:
            if ln == "Details":
                seen_details = True
            continue
        if not seen_details and re.match(
            r"^(Prompted|Asked in AI Mode|Asked|Searched for)\b", ln, re.IGNORECASE
        ):
            prompt_text = re.sub(
                r"^(Prompted|Asked in AI Mode|Asked|Searched for)\s*:?\s*",
                "",
                ln,
                flags=re.IGNORECASE,
            ).strip()
            continue
        if re.match(
            r"^(?:Today|Yesterday|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})"
            r"(?:,?\s*\d{4})?(?:\s+at)?\s+\d{1,2}:\d{2}\s*(?:AM|PM)?$",
            ln,
            flags=re.IGNORECASE,
        ):
            time_text = ln
            continue
        if ln.startswith("https://gemini.google.com/") or ln.startswith("https://www.google.com/"):
            continue
        if seen_details:
            body_lines.append(ln)

    source_chat_id: str | None = None
    source_url: str | None = None
    for href in links:
        m_gem = re.search(r"gemini\.google\.com/(?:u/\d+/)?app/([a-zA-Z0-9_-]{8,})", href)
        if m_gem:
            source_chat_id = m_gem.group(1)
            source_url = f"https://gemini.google.com/app/{source_chat_id}"
            break
        if "udm=50" in href:
            source_url = _canonicalize_aimode_url(href)
            m_mtid = re.search(r"mtid=([a-zA-Z0-9_-]+)", source_url)
            if m_mtid:
                source_chat_id = f"aimode_{m_mtid.group(1)}"
            break

    return {
        "prompt": prompt_text or fallback_prompt,
        "response": "\n".join(body_lines).strip(),
        "time_text": time_text,
        "source_chat_id": source_chat_id,
        "source_url": source_url,
    }


async def _sync_myactivity_page(
    page: Any,
    max_chats: int,
    base_dir: Path | str | None,
) -> list[dict[str, Any]]:
    """Scrapes Google's central My Activity hub (myactivity.google.com/product/gemini & search)."""
    _log_progress(f"Starting My Activity sync (max_chats={max_chats})...")
    urls = [
        ("https://myactivity.google.com/product/gemini", "myactivity_gemini"),
        ("https://myactivity.google.com/product/search", "myactivity_search_ai"),
    ]
    synced: list[dict[str, Any]] = []
    for url, src_tag in urls:
        _log_progress(f"[myactivity] Scanning {src_tag} ({url})...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2.5)
            cards = await page.evaluate(
                r"""(maxItems) => {
                    const out = [];
                    const els = document.querySelectorAll(
                        'c-wiz [role="listitem"], [data-date] [role="region"], article'
                    );
                    const badges = /^(Gemini Apps|AI Mode|Search|Google Search|YouTube|Maps)$/i;
                    const metaRe = /^\d{1,2}:\d{2}\s*(?:AM|PM)?\s*(?:•.*)?(?:Details)?$/i;
                    els.forEach((el, idx) => {
                        if (out.length >= maxItems) return;
                        const txt = (el.innerText || '').trim();
                        if (!txt || /Cleared previous feedback/i.test(txt)) return;
                        if (/prompted|asked|ai mode|gemini|searched for/i.test(txt)) {
                            let lines = txt.split('\n').map(l => l.trim()).filter(Boolean);
                            while (lines.length > 1 && badges.test(lines[0])) {
                                lines.shift();
                            }
                            let timeStr = '';
                            const bodyLines = [];
                            for (const l of lines.slice(1)) {
                                if (metaRe.test(l) || /^Details$/i.test(l)) {
                                    const tm = l.match(/\d{1,2}:\d{2}\s*(?:AM|PM)?/i);
                                    if (tm && !timeStr) timeStr = tm[0];
                                    continue;
                                }
                                bodyLines.push(l);
                            }
                            const dateHolder = el.closest('[data-date]');
                            const dataDate = dateHolder?.getAttribute('data-date') || '';
                            const markerAttr = `data-gah-idx="${idx}"`;
                            el.setAttribute('data-gah-idx', String(idx));
                            const hasDetailsBtn = !!el.querySelector(
                                'a.WFTFcf, a[aria-label*="details" i], ' +
                                'button[aria-label*="details" i]'
                            );
                            out.push({
                                idx,
                                title: lines[0] || 'My Activity AI Entry',
                                body: bodyLines.join('\n'),
                                dataDate,
                                timeStr,
                                hasDetailsBtn,
                            });
                        }
                    });
                    return out;
                }""",
                max_chats,
            )

            _log_progress(f"[myactivity:{src_tag}] Found {len(cards)} entry card(s).")
            for card_num, card in enumerate(cards[:max_chats], start=1):
                title_clean = re.sub(
                    r"^(Prompted|Asked in AI Mode|Asked|Searched for)\s*:?\s*",
                    "",
                    card["title"],
                    flags=re.IGNORECASE,
                ).strip()
                prompt_full = title_clean or card["title"]
                response_full = str(card.get("body") or "").strip()
                time_raw = str(card.get("timeStr") or "")
                data_date = str(card.get("dataDate") or "")
                source_chat_id: str | None = None
                entry_url = url

                if card.get("hasDetailsBtn") and card.get("idx") is not None:
                    try:
                        clicked = await page.evaluate(
                            """(idx) => {
                                const el = document.querySelector(`[data-gah-idx="${idx}"]`);
                                if (!el) return false;
                                const btn = el.querySelector(
                                    'a.WFTFcf, a[aria-label*="details" i], ' +
                                    'button[aria-label*="details" i]'
                                );
                                if (!btn) return false;
                                btn.click();
                                return true;
                            }""",
                            card["idx"],
                        )
                        if clicked:
                            dialog_data = None
                            for _ in range(10):
                                await asyncio.sleep(0.25)
                                dialog_data = await page.evaluate(
                                    """() => {
                                        const d = document.querySelector(
                                            '[role="dialog"], [aria-modal="true"]'
                                        );
                                        if (!d) return null;
                                        const linkList = Array.from(
                                            d.querySelectorAll('a[href]')
                                        ).map(a => a.href);
                                        return {
                                            text: (d.innerText || '').trim(),
                                            links: linkList,
                                        };
                                    }"""
                                )
                                if isinstance(dialog_data, dict) and dialog_data.get("text"):
                                    break
                            if isinstance(dialog_data, dict) and dialog_data.get("text"):
                                parsed_dlg = _parse_myactivity_dialog_text(
                                    dialog_data["text"],
                                    dialog_data.get("links") or [],
                                    fallback_prompt=prompt_full,
                                )
                                prompt_full = parsed_dlg["prompt"] or prompt_full
                                if parsed_dlg["response"]:
                                    response_full = parsed_dlg["response"]
                                if parsed_dlg["time_text"]:
                                    time_raw = parsed_dlg["time_text"]
                                source_chat_id = parsed_dlg.get("source_chat_id")
                                if parsed_dlg.get("source_url"):
                                    entry_url = parsed_dlg["source_url"]
                            await page.evaluate(
                                """() => {
                                    const closeBtn = document.querySelector(
                                        '[role="dialog"] button[aria-label*="close" i], ' +
                                        '[role="dialog"] button[aria-label*="dismiss" i]'
                                    );
                                    if (closeBtn) closeBtn.click();
                                }"""
                            )
                            await asyncio.sleep(0.15)
                    except Exception:
                        pass

                iso_ts = _parse_surface_timestamp(time_raw, data_date=data_date)
                msgs: list[dict[str, Any]] = [
                    {
                        "role": "user",
                        "content": prompt_full,
                        **({"timestamp": iso_ts} if iso_ts else {}),
                    }
                ]
                if response_full:
                    msgs.append(
                        {
                            "role": "assistant",
                            "content": response_full,
                            **({"timestamp": iso_ts} if iso_ts else {}),
                        }
                    )

                hash_seed = (
                    f"{src_tag}:{source_chat_id or ''}:{iso_ts or card_num}:"
                    f"{prompt_full}:{response_full[:80]}"
                )
                cid_hash = hashlib.sha1(hash_seed.encode()).hexdigest()[:14]
                synced.append(
                    upsert_chat(
                        chat_id=f"{src_tag}_{cid_hash}",
                        title=(prompt_full[:80] or "Google AI Activity"),
                        messages=msgs,
                        source=src_tag,
                        url=entry_url,
                        created_at=iso_ts,
                        updated_at=iso_ts,
                        base_dir=base_dir,
                        source_chat_id=source_chat_id,
                    )
                )
        except Exception as exc:
            synced.append({"url": url, "error": str(exc)})
    _log_progress(
        f"[myactivity] Completed: {len([r for r in synced if 'markdown_path' in r])} synced."
    )
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
                return {
                    "error": "Could not find Chrome or Edge executable on this machine.",
                    "suggestion": (
                        "Install Google Chrome or Microsoft Edge, then run "
                        "'google-ai-history doctor' to verify prerequisites."
                    ),
                }
            _launch_chrome_visible(chrome_exe, paths["profile"], "https://gemini.google.com/app")
            for _ in range(30):
                await asyncio.sleep(1.0)
                try:
                    browser = await p.chromium.connect_over_cdp(cdp_url)
                    break
                except Exception:
                    continue

        if not browser:
            return {
                "error": "Failed to connect to Chrome over CDP port 9222.",
                "suggestion": (
                    "Run 'google-ai-history setup' on a desktop environment with Chrome/Edge "
                    "to authenticate your Google account."
                ),
            }

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        if "google.com" not in page.url or "SignOutOptions" in page.url:
            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")

        deadline = time.time() + login_timeout_sec
        logged_in = False
        clicked_sign_in = False
        while time.time() < deadline:
            url = page.url
            if "accounts.google.com" not in url and "google.com" in url:
                has_chat_ui = await page.evaluate(
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
                if not has_chat_ui:
                    clicked_sign_in = True
                else:
                    logged_in = True
                    break
            await asyncio.sleep(2.0)

        if not logged_in:
            return {
                "status": "login_required",
                "message": (
                    "Authentication required. Please sign into your Google account in the "
                    "opened Chrome window and run 'google-ai-history setup' or retry."
                ),
                "suggestion": "Run 'google-ai-history setup' to complete one-time Google sign-in.",
            }

        synced_results: list[dict[str, Any]] = []
        surfaces: dict[str, dict[str, Any]] = {}

        def _record_surface(name: str, items: list[dict[str, Any]]) -> None:
            synced_results.extend(items)
            errs = [
                {k: v for k, v in r.items() if k in ("chat_id", "url", "error")}
                for r in items
                if "error" in r
            ]
            unique_ok = {r["chat_id"] for r in items if "markdown_path" in r and r.get("chat_id")}
            surfaces[name] = {
                "synced_count": len(unique_ok),
                "error_count": len(errs),
                "errors": errs[:20],
            }

        if target in ("gemini", "all"):
            _record_surface("gemini", await _sync_gemini_app_page(page, max_chats, base_dir))
        if target in ("aimode", "all"):
            _record_surface("aimode", await _sync_search_ai_mode_page(page, max_chats, base_dir))
        if target in ("aistudio", "all"):
            _record_surface("aistudio", await _sync_aistudio_page(page, max_chats, base_dir))
        if target in ("notebooklm", "all"):
            _record_surface("notebooklm", await _sync_notebooklm_page(page, max_chats, base_dir))
        if target in ("myactivity", "all"):
            ma_items = await _sync_myactivity_page(page, max_chats, base_dir)
            _record_surface("myactivity", ma_items)
            if target == "all":
                known_gem_ids = {
                    r.get("chat_id")
                    for r in synced_results
                    if "markdown_path" in r and r.get("chat_id")
                }
                discovered_gemini: list[dict[str, str]] = []
                seen_disc: set[str] = set()
                for item in ma_items:
                    scid = item.get("source_chat_id")
                    if (
                        scid
                        and not str(scid).startswith("aimode_")
                        and scid not in known_gem_ids
                        and scid not in seen_disc
                    ):
                        seen_disc.add(scid)
                        discovered_gemini.append(
                            {
                                "id": str(scid),
                                "url": f"https://gemini.google.com/app/{scid}",
                                "title": str(item.get("title") or ""),
                            }
                        )
                remaining = max(0, max_chats - len(known_gem_ids))
                if discovered_gemini and remaining > 0:
                    _log_progress(
                        f"[gemini] Backfilling {min(len(discovered_gemini), remaining)} "
                        f"thread(s) discovered via My Activity..."
                    )
                    extra_res = await _sync_gemini_app_page(
                        page,
                        remaining,
                        base_dir,
                        extra_chats=discovered_gemini,
                    )
                    _record_surface("gemini_from_myactivity", extra_res)

        deduped_chats: list[dict[str, Any]] = []
        seen_result_idx: dict[str, int] = {}
        for r in synced_results:
            cid = r.get("chat_id")
            if cid and "markdown_path" in r:
                cid_str = str(cid)
                if cid_str in seen_result_idx:
                    deduped_chats[seen_result_idx[cid_str]] = r
                else:
                    seen_result_idx[cid_str] = len(deduped_chats)
                    deduped_chats.append(r)
            else:
                deduped_chats.append(r)

        return {
            "status": "ok",
            "target": target,
            "synced_count": len([r for r in deduped_chats if "markdown_path" in r]),
            "surfaces": surfaces,
            "synced_chats": deduped_chats,
        }


def _detect_url_surface_and_id(url: str) -> tuple[str, str]:
    """Classifies a Google AI URL into (source_tag, chat_id)."""
    u = _normalize_google_ai_url(url)
    m_share = re.search(
        r"(?:(?:gemini\.google\.com|g\.co/gemini)/share|share\.gemini\.google)/([a-zA-Z0-9_-]+)",
        u,
    )
    if m_share:
        return ("url_gemini_share", f"gemini_share_{m_share.group(1)}")

    m_app = re.search(r"gemini\.google\.com/(?:u/\d+/)?app/([a-zA-Z0-9_-]+)", u)
    if m_app:
        return ("browser_gemini", m_app.group(1))

    m_ais = re.search(r"aistudio\.google\.com/(?:app/)?prompts/([a-zA-Z0-9_-]+)", u)
    if m_ais:
        return ("browser_aistudio", f"aistudio_{m_ais.group(1)}")

    m_nb = re.search(r"(?:notebooklm|notebook|gemini)\.google\.com/notebook/([a-zA-Z0-9_-]+)", u)
    if m_nb:
        return ("browser_notebooklm", f"notebooklm_{m_nb.group(1)}")

    if "udm=50" in u or "aimode" in u:
        canon = _canonicalize_aimode_url(u)
        m_mtid = re.search(r"[?&]mtid=([a-zA-Z0-9_-]+)", canon)
        if m_mtid:
            return ("browser_search_ai_mode", f"aimode_{m_mtid.group(1)}")
        cid = hashlib.sha1(canon.encode()).hexdigest()[:14]
        return ("browser_search_ai_mode", f"aimode_{cid}")

    cid = hashlib.sha1(u.encode()).hexdigest()[:14]
    return ("url_google_ai", f"google_ai_{cid}")


def _fetch_public_share_via_http(url: str) -> dict[str, Any] | None:
    """Fetches a public Gemini share link in ~1s via HTTP + batchexecute RPC without Chrome/auth."""
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            final_url = resp.geturl()
    except Exception:
        return None

    m_share = re.search(r"/share/([a-zA-Z0-9_-]+)", final_url) or re.search(
        r"(?:share\.gemini\.google|/share)/([a-zA-Z0-9_-]+)", url
    )
    share_id = m_share.group(1) if m_share else ""
    m_bl = re.search(r'"cfb2h":"([^"]+)"', html)
    bl = m_bl.group(1) if m_bl else "boq_gemini-web-uiserver_20261106.06_p0"
    m_sid = re.search(r'"FdrFJe":"([^"]+)"', html)
    fsid = m_sid.group(1) if m_sid else ""

    if share_id:
        rpc_id = "ujx1Bf"
        req_payload = json.dumps([[[rpc_id, json.dumps([None, share_id]), None, "generic"]]])
        batch_url = (
            f"https://gemini.google.com/_/BardChatUi/data/batchexecute"
            f"?rpcids={rpc_id}&source-path=%2Fshare%2F{urllib.parse.quote(share_id)}"
            f"&bl={urllib.parse.quote(bl)}&f.sid={urllib.parse.quote(fsid)}&hl=en&_reqid=12345&rt=c"
        )
        data = urllib.parse.urlencode({"f.req": req_payload}).encode("utf-8")
        req_rpc = urllib.request.Request(
            batch_url,
            data=data,
            headers={
                "User-Agent": ua,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
        )
        try:
            with urllib.request.urlopen(req_rpc, timeout=15) as r_rpc:
                resp_text = r_rpc.read().decode("utf-8", errors="replace")
            for line in resp_text.splitlines():
                if '"wrb.fr"' not in line or rpc_id not in line:
                    continue
                outer = json.loads(line)
                if (
                    isinstance(outer, list)
                    and outer
                    and isinstance(outer[0], list)
                    and len(outer[0]) > 2
                    and isinstance(outer[0][2], str)
                ):
                    inner = json.loads(outer[0][2])
                    if not (isinstance(inner, list) and inner and isinstance(inner[0], list)):
                        continue
                    root = inner[0]
                    turns = root[1] if len(root) > 1 and isinstance(root[1], list) else []
                    messages: list[dict[str, Any]] = []
                    for turn in turns:
                        if not isinstance(turn, list) or len(turn) < 4:
                            continue
                        # Extract user prompt at turn[2][0][0]
                        if (
                            isinstance(turn[2], list)
                            and turn[2]
                            and isinstance(turn[2][0], list)
                            and turn[2][0]
                            and isinstance(turn[2][0][0], str)
                        ):
                            u_txt = _strip_directional_chars(turn[2][0][0])
                            if u_txt:
                                messages.append({"role": "user", "content": u_txt})
                        # Extract model response markdown at turn[3][0][0][1][0]
                        if (
                            isinstance(turn[3], list)
                            and turn[3]
                            and isinstance(turn[3][0], list)
                            and turn[3][0]
                            and isinstance(turn[3][0][0], list)
                            and len(turn[3][0][0]) > 1
                            and isinstance(turn[3][0][0][1], list)
                            and turn[3][0][0][1]
                            and isinstance(turn[3][0][0][1][0], str)
                        ):
                            a_txt = _strip_directional_chars(turn[3][0][0][1][0])
                            if a_txt:
                                messages.append({"role": "assistant", "content": a_txt})
                    title = ""
                    if (
                        len(root) > 2
                        and isinstance(root[2], list)
                        and len(root[2]) > 1
                        and isinstance(root[2][1], str)
                    ):
                        title = _strip_directional_chars(root[2][1])
                    if messages:
                        if not title:
                            title = messages[0]["content"].splitlines()[0][:80]
                        return {"title": title, "messages": messages}
        except Exception:
            pass

    return None


def _probe_profile_google_cookies(profile_dir: Path) -> int:
    """Counts Google auth cookies (SID, SAPISID, __Secure-1PSID, __Secure-3PSID) in Chrome DB."""
    candidates = [
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
    ]
    cookie_db = next((p for p in candidates if p.is_file()), None)
    if not cookie_db:
        return 0

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "Cookies"
            shutil.copy2(cookie_db, tmp_path)
            for suffix in ("-wal", "-shm"):
                sidecar = cookie_db.with_name(cookie_db.name + suffix)
                if sidecar.is_file():
                    shutil.copy2(sidecar, Path(tmpdir) / ("Cookies" + suffix))
            conn = sqlite3.connect(str(tmp_path))
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM cookies "
                    "WHERE host_key LIKE '%google.com%' "
                    "AND name IN ('SID', 'SAPISID', '__Secure-1PSID', '__Secure-3PSID')"
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
    except Exception:
        return 0


def _check_registered_mcp_configs(
    config_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """Inspects standard MCP client config files and verifies command and --directory paths."""
    home = Path.home()
    candidates = config_paths or [
        home / ".gemini" / "config" / "mcp_config.json",
        home / ".gemini" / "antigravity" / "mcp_config.json",
        home / ".cursor" / "mcp.json",
        home / ".claude.json",
    ]
    results: list[dict[str, Any]] = []
    for cfg_path in candidates:
        if not cfg_path.is_file():
            continue
        entry_info: dict[str, Any] = {
            "path": str(cfg_path),
            "configured": False,
            "ok": True,
            "issues": [],
        }
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            entry_info["ok"] = False
            entry_info["issues"].append(f"Failed to parse JSON: {exc}")
            results.append(entry_info)
            continue

        servers = raw.get("mcpServers") if isinstance(raw, dict) else None
        srv = servers.get("google-ai-history") if isinstance(servers, dict) else None
        if not isinstance(srv, dict):
            results.append(entry_info)
            continue

        entry_info["configured"] = True
        cmd = str(srv.get("command") or "").strip()
        args = [str(a) for a in (srv.get("args") or [])]
        entry_info["command"] = cmd
        entry_info["args"] = args

        if not cmd:
            entry_info["ok"] = False
            entry_info["issues"].append("Missing 'command' field.")
        elif "/" in cmd or "\\" in cmd or cmd.lower().endswith(".exe"):
            if not Path(cmd).is_file():
                entry_info["ok"] = False
                entry_info["issues"].append(f"Command path does not exist: {cmd}")
        elif shutil.which(cmd) is None:
            entry_info["ok"] = False
            entry_info["issues"].append(f"Command '{cmd}' was not found on PATH.")

        if "--directory" in args:
            dir_idx = args.index("--directory")
            if dir_idx + 1 >= len(args):
                entry_info["ok"] = False
                entry_info["issues"].append("'--directory' flag has no value.")
            else:
                dir_val = args[dir_idx + 1]
                entry_info["directory"] = dir_val
                if not Path(dir_val).is_dir():
                    entry_info["ok"] = False
                    entry_info["issues"].append(f"--directory path does not exist: {dir_val}")

        results.append(entry_info)
    return results


def check_system_health(
    base_dir: Path | str | None = None,
    cdp_url: str = "http://127.0.0.1:9222",
) -> dict[str, Any]:
    """Checks system prerequisites, dependencies, Chrome/CDP status, and real auth cookie state."""
    paths = ensure_dirs(base_dir)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 11)

    deps: dict[str, bool] = {}
    for mod_name in ("bs4", "typing_extensions", "fastmcp", "playwright"):
        try:
            __import__(mod_name)
            deps[mod_name] = True
        except Exception:
            deps[mod_name] = False

    chrome_exe = find_chrome_executable()
    cdp_active = False
    try:
        with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=2) as r:
            cdp_active = r.status == 200
    except Exception:
        cdp_active = False

    profile_dir = paths["profile"]
    profile_initialized = (profile_dir / "Default").is_dir()
    google_auth_cookie_count = _probe_profile_google_cookies(profile_dir)
    auth_ready = bool(chrome_exe and google_auth_cookie_count > 0)
    mcp_configs = _check_registered_mcp_configs()
    mcp_configs_ok = all(c.get("ok", True) for c in mcp_configs)

    recommendations: list[str] = []
    if not all(deps.values()):
        missing = [k for k, v in deps.items() if not v]
        recommendations.append(
            f"Install missing Python dependencies: pip install {' '.join(missing)}"
        )
    if not chrome_exe:
        recommendations.append(
            "Chrome/Edge not found. Public Gemini share links (share.gemini.google/...) still work "
            "via direct HTTP without a browser, but private chats require Chrome or Edge."
        )
    elif not auth_ready:
        recommendations.append(
            "No Google auth session found in chrome_profile. Run 'google-ai-history setup' "
            "to open Chrome and sign into Google once for private chats."
        )
    for cfg in mcp_configs:
        if not cfg.get("ok", True):
            for issue in cfg.get("issues", []):
                recommendations.append(f"MCP config issue in {cfg['path']}: {issue}")

    return {
        "status": "ok" if (py_ok and all(deps.values()) and mcp_configs_ok) else "warning",
        "python_version": py_ver,
        "python_supported": py_ok,
        "dependencies": deps,
        "chrome_executable": chrome_exe,
        "cdp_port_9222_active": cdp_active,
        "profile_dir": str(profile_dir),
        "profile_initialized": profile_initialized,
        "google_auth_cookie_count": google_auth_cookie_count,
        "public_share_links_ready": True,
        "authenticated_surfaces_ready": auth_ready,
        "mcp_configs_ok": mcp_configs_ok,
        "mcp_configs": mcp_configs,
        "recommendations": recommendations,
    }


async def setup_browser_auth(
    login_timeout_sec: int = 180,
    cdp_url: str = "http://127.0.0.1:9222",
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Opens the dedicated Chrome profile and waits for one-time Google authentication."""
    res = await sync_from_browser(
        max_chats=1,
        login_timeout_sec=login_timeout_sec,
        cdp_url=cdp_url,
        target="gemini",
        base_dir=base_dir,
    )
    if res.get("status") == "ok":
        paths = ensure_dirs(base_dir)
        return {
            "status": "authenticated",
            "message": "Google account authenticated and persistent session saved.",
            "profile_dir": str(paths["profile"]),
        }
    return res


async def fetch_chat_from_url(
    url: str,
    login_timeout_sec: int = 180,
    cdp_url: str = "http://127.0.0.1:9222",
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Navigates to a Google AI chat URL, saves it locally, and returns all turns."""
    from playwright.async_api import async_playwright

    from google_ai_history.db import get_chat

    clean_url = _normalize_google_ai_url(url)
    if not clean_url.startswith(("http://", "https://")):
        return {"error": f"Invalid URL '{url}'. Must start with http:// or https://."}

    source_tag, chat_id = _detect_url_surface_and_id(clean_url)
    is_public_share = source_tag == "url_gemini_share"
    paths = ensure_dirs(base_dir)

    # Zero-browser HTTP fast-path for public Gemini share links (~1s, no Chrome/login needed)
    if is_public_share:
        http_extracted = await asyncio.to_thread(_fetch_public_share_via_http, clean_url)
        if http_extracted and http_extracted.get("messages"):
            title = _strip_directional_chars(
                http_extracted.get("title") or f"Google AI Chat {chat_id}"
            )
            upsert_chat(
                chat_id=chat_id,
                title=title[:100],
                messages=http_extracted["messages"],
                source=source_tag,
                url=clean_url,
                base_dir=base_dir,
            )
            full_record = get_chat(chat_id, base_dir=base_dir)
            return {
                "status": "ok",
                "method": "http_direct",
                "chat": full_record,
            }

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception:
            chrome_exe = find_chrome_executable()
            if not chrome_exe:
                return {
                    "error": "Could not find Chrome or Edge executable on this machine.",
                    "suggestion": (
                        "Install Chrome/Edge for private URLs, or use a public share link "
                        "(https://share.gemini.google/...) which works without Chrome."
                    ),
                }
            _launch_chrome_visible(chrome_exe, paths["profile"], clean_url)
            for _ in range(30):
                await asyncio.sleep(1.0)
                try:
                    browser = await p.chromium.connect_over_cdp(cdp_url)
                    break
                except Exception:
                    continue

        if not browser:
            return {
                "error": "Failed to connect to Chrome over CDP port 9222.",
                "suggestion": (
                    "Run 'google-ai-history setup' on a desktop environment to sign in once, "
                    "or use a public Gemini share link (https://share.gemini.google/...)."
                ),
            }

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass
        await page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2.5)

        auth_required_surface = source_tag in (
            "browser_gemini",
            "browser_aistudio",
            "browser_notebooklm",
        )
        if auth_required_surface:
            deadline = time.time() + login_timeout_sec
            logged_in = False
            clicked_sign_in = False
            while time.time() < deadline:
                cur_url = page.url
                if "CookieMismatch" in cur_url:
                    break
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
                if "CookieMismatch" in getattr(page, "url", ""):
                    return {
                        "status": "error",
                        "reason": "cookie_mismatch",
                        "final_url": getattr(page, "url", ""),
                        "error": (
                            "Google detected mismatched or corrupted cookies in the Chrome profile "
                            "(accounts.google.com/CookieMismatch)."
                        ),
                        "suggestion": (
                            "Delete ~/.gemini/google_ai_history/chrome_profile to reset cookies, "
                            "then run 'google-ai-history setup' to start fresh."
                        ),
                    }
                return {
                    "status": "login_required",
                    "reason": "login_required",
                    "message": (
                        "Authentication required. Please sign into your Google account in the "
                        "opened Chrome window and run 'google-ai-history setup' or retry."
                    ),
                    "suggestion": "Run 'google-ai-history setup' for one-time Google sign-in.",
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

        incomplete: str | None = None
        attachments: list[dict[str, Any]] = []
        created_at: str | None = None
        stored_url = clean_url

        if source_tag == "browser_gemini":
            gem_parsed = await _extract_gemini_chat_from_page(page)
            msgs = gem_parsed["messages"]
            title = gem_parsed["title"]
            incomplete = gem_parsed.get("incomplete")
            created_at = gem_parsed.get("created_at")
        elif source_tag == "browser_aistudio":
            ais_parsed = await _extract_aistudio_from_page(page)
            msgs = ais_parsed["messages"]
            title = ais_parsed["title"]
            attachments = ais_parsed.get("attachments") or []
        else:
            if source_tag == "browser_search_ai_mode":
                stored_url = _canonicalize_aimode_url(clean_url)
            extracted = (
                await page.evaluate(
                    """() => {
                    const messages = [];
                    // 1. Gemini Share turns
                    const geminiNodes = document.querySelectorAll(
                        'user-query, model-response, share-turn-viewer response-container, ' +
                        '[data-message-author-role]'
                    );
                    if (geminiNodes.length > 0) {
                        geminiNodes.forEach(node => {
                            const tag = node.tagName.toLowerCase();
                            const roleAttr = node.getAttribute('data-message-author-role');
                            if (tag === 'user-query' || roleAttr === 'user') {
                                const qEl = node.querySelector(
                                    '.query-text, .user-query-text'
                                ) || node;
                                const txt = (qEl.innerText || '')
                                    .replace(/^You said[^\\n]*\\n+/i, '')
                                    .replace(/^You said\\s*/i, '')
                                    .trim();
                                if (txt) messages.push({ role: 'user', content: txt });
                            } else if (
                                tag === 'model-response' ||
                                tag === 'response-container' ||
                                roleAttr === 'model'
                            ) {
                                const md = node.querySelector(
                                    'message-content, .markdown, .model-response-text'
                                ) || node;
                                const txt = (md.innerText || '')
                                    .replace(/^Gemini said\\s*/i, '')
                                    .trim();
                                if (txt) messages.push({ role: 'assistant', content: txt });
                            }
                        });
                    }

                    // 2. Search AI Mode (udm=50) multi-turn extraction
                    if (messages.length === 0) {
                        const mainEl =
                            document.querySelector('main, [role="main"]') || document.body;
                        let text = (mainEl?.innerText || '').trim();
                        const marker = 'AI Mode Conversation:';
                        const idx = text.indexOf(marker);
                        if (idx !== -1) {
                            text = text.slice(idx).trim();
                            const pattern = new RegExp(
                                'You said:\\\\s*([^\\\\n]+)\\\\n(?:[^\\\\n]*\\\\n){0,4}?' +
                                'AI Mode reply for [^\\\\n]+\\\\n([\\\\s\\\\S]*?)' +
                                '(?=\\\\nYou said:|\\\\nSkip to previous prompt|' +
                                '\\\\nAI Mode reply|$)',
                                'g'
                            );
                            let match;
                            while ((match = pattern.exec(text)) !== null) {
                                const p = (match[1] || '').trim();
                                let r = (match[2] || '').trim()
                                    .replace(/\\nShow all\\s*$/, '').trim();
                                if (p) messages.push({ role: 'user', content: p });
                                if (r) messages.push({ role: 'assistant', content: r });
                            }
                        }
                    }

                    // 3. Fallback for NotebookLM or generic Google AI page
                    if (messages.length === 0) {
                        const mainEl = document.querySelector('main') || document.body;
                        const txt = (mainEl?.innerText || '').trim();
                        if (txt) messages.push({ role: 'assistant', content: txt });
                    }

                    const titleEl = document.querySelector(
                        '[data-test-id="conversation-title"], ' +
                        '.conversation-title:not(.picker-secondary-text), h1'
                    );
                    let pageTitle = (titleEl?.innerText || document.title || '').trim();
                    const mAiTitle = pageTitle.match(/AI Mode Conversation:\\s*([^\\n]+)/i);
                    if (mAiTitle) {
                        pageTitle = mAiTitle[1].trim();
                    }
                    return { pageTitle: pageTitle || document.title || '', messages };
                }"""
                )
                or {}
            )
            msgs = extracted.get("messages") or []
            raw_title = _clean_gemini_title(str(extracted.get("pageTitle") or ""))
            if not raw_title and msgs:
                raw_title = _strip_directional_chars(
                    str(msgs[0].get("content") or "")
                ).splitlines()[0][:80]
            title = raw_title or f"Google AI Chat {chat_id}"
        if not msgs:
            final_url = str(getattr(page, "url", "") or "")
            if "CookieMismatch" in final_url:
                return {
                    "status": "error",
                    "reason": "cookie_mismatch",
                    "final_url": final_url,
                    "error": (
                        "Google detected mismatched or corrupted cookies in the Chrome profile "
                        "(accounts.google.com/CookieMismatch)."
                    ),
                    "suggestion": (
                        "Delete ~/.gemini/google_ai_history/chrome_profile to reset cookies, "
                        "then run 'google-ai-history setup' to start fresh."
                    ),
                }
            redirected_to_home = source_tag == "browser_gemini" and final_url.rstrip("/").endswith(
                "gemini.google.com/app"
            )
            if "accounts.google.com" in final_url or redirected_to_home:
                return {
                    "status": "login_required",
                    "reason": "login_required",
                    "final_url": final_url,
                    "error": (
                        f"Google authentication is required to access {clean_url} "
                        f"(redirected to {final_url})."
                    ),
                    "suggestion": "Run 'google-ai-history setup' to sign into your Google account.",
                }
            return {
                "status": "error",
                "reason": "selector_miss_or_empty_thread",
                "final_url": final_url,
                "error": f"No conversation content could be extracted from {clean_url}",
                "suggestion": (
                    "Verify the URL points to a non-empty conversation and run "
                    "'google-ai-history doctor' to check authentication state."
                ),
            }

        upsert_chat(
            chat_id=chat_id,
            title=title[:100],
            messages=msgs,
            source=source_tag,
            url=stored_url,
            created_at=created_at,
            base_dir=base_dir,
            incomplete=incomplete,
            attachments=attachments or None,
        )
        full_record = get_chat(chat_id, base_dir=base_dir)
        return {
            "status": "ok",
            "chat": full_record,
        }
