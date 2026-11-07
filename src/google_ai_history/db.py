import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def get_data_dir(custom_dir: Path | str | None = None) -> Path:
    if custom_dir is not None:
        return Path(custom_dir)
    env_dir = os.environ.get("GOOGLE_AI_HISTORY_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".gemini" / "google_ai_history"


def ensure_dirs(base_dir: Path | str | None = None) -> dict[str, Path]:
    root = get_data_dir(base_dir)
    md_dir = root / "markdown"
    profile_dir = root / "chrome_profile"
    root.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "markdown": md_dir,
        "db": root / "chats.db",
        "profile": profile_dir,
    }


def get_conn(base_dir: Path | str | None = None) -> sqlite3.Connection:
    paths = ensure_dirs(base_dir)
    conn = sqlite3.connect(str(paths["db"]))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            url TEXT,
            created_at TEXT,
            updated_at TEXT,
            message_count INTEGER DEFAULT 0,
            markdown_path TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT,
            FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chats_fts USING fts5(
            chat_id UNINDEXED,
            title,
            full_text
        );
        """
    )
    return conn


def slugify(text: str, max_len: int = 60) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip()).strip("_")
    return (cleaned[:max_len] or "untitled").lower()


def upsert_chat(
    chat_id: str,
    title: str,
    messages: list[dict[str, Any]],
    source: str = "browser",
    url: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Saves or updates a chat in SQLite FTS5 and writes a local Markdown file."""
    paths = ensure_dirs(base_dir)
    now_iso = datetime.now(UTC).isoformat()
    created_at = created_at or now_iso
    updated_at = updated_at or now_iso
    title = (title or "Untitled Google AI Chat").strip()

    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", chat_id)
    md_filename = f"{slugify(title)}_{safe_id[:16]}.md"
    md_path = paths["markdown"] / md_filename

    md_lines = [
        f"# {title}",
        "",
        f"- **Chat ID**: `{chat_id}`",
        f"- **Source**: `{source}`",
    ]
    if url:
        md_lines.append(f"- **URL**: {url}")
    md_lines.extend(
        [
            f"- **Updated**: `{updated_at}`",
            f"- **Messages**: {len(messages)}",
            "",
            "---",
            "",
        ]
    )

    full_text_parts = [title]
    for msg in messages:
        role = str(msg.get("role", "user")).upper()
        content = str(msg.get("content") or "").strip()
        ts = msg.get("timestamp")
        header = f"### {role}" + (f" ({ts})" if ts else "")
        md_lines.extend([header, "", content, "", "---", ""])
        full_text_parts.append(f"{role}: {content}")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    full_text = "\n\n".join(full_text_parts)

    conn = get_conn(base_dir)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO chats (
                    chat_id, title, source, url, created_at, updated_at,
                    message_count, markdown_path, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    source = excluded.source,
                    url = COALESCE(excluded.url, chats.url),
                    updated_at = excluded.updated_at,
                    message_count = excluded.message_count,
                    markdown_path = excluded.markdown_path,
                    raw_json = excluded.raw_json
                """,
                (
                    chat_id,
                    title,
                    source,
                    url,
                    created_at,
                    updated_at,
                    len(messages),
                    str(md_path),
                    json.dumps(messages, ensure_ascii=False),
                ),
            )
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            for idx, msg in enumerate(messages):
                conn.execute(
                    """
                    INSERT INTO messages (chat_id, turn_index, role, content, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        idx,
                        str(msg.get("role", "user")),
                        str(msg.get("content") or "").strip(),
                        msg.get("timestamp"),
                    ),
                )
            conn.execute("DELETE FROM chats_fts WHERE chat_id = ?", (chat_id,))
            conn.execute(
                "INSERT INTO chats_fts (chat_id, title, full_text) VALUES (?, ?, ?)",
                (chat_id, title, full_text),
            )
    finally:
        conn.close()

    return {
        "chat_id": chat_id,
        "title": title,
        "message_count": len(messages),
        "markdown_path": str(md_path),
    }


def list_chats(
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    paths = ensure_dirs(base_dir)
    conn = get_conn(base_dir)
    try:
        total_row = conn.execute("SELECT COUNT(*) AS cnt FROM chats").fetchone()
        total = int(total_row["cnt"]) if total_row else 0
        if source:
            rows = conn.execute(
                """
                SELECT chat_id, title, source, url, created_at, updated_at,
                       message_count, markdown_path
                FROM chats
                WHERE source = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (source, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT chat_id, title, source, url, created_at, updated_at,
                       message_count, markdown_path
                FROM chats
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "total_chats": total,
            "returned": len(rows),
            "markdown_directory": str(paths["markdown"]),
            "chats": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def search_chats(
    query: str,
    limit: int = 20,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    conn = get_conn(base_dir)
    try:
        tokens = [re.sub(r'["\'^*():-]', " ", tok).strip() for tok in query.split()]
        tokens = [t for t in tokens if t]
        if not tokens:
            return {"query": query, "matches": 0, "results": []}
        fts_query = " OR ".join(f'"{t}"' for t in tokens)

        rows = conn.execute(
            """
            SELECT
                c.chat_id,
                c.title,
                c.source,
                c.url,
                c.updated_at,
                c.message_count,
                c.markdown_path,
                snippet(chats_fts, 2, '[[', ']]', ' ... ', 40) AS snippet
            FROM chats_fts
            JOIN chats c ON c.chat_id = chats_fts.chat_id
            WHERE chats_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return {
            "query": query,
            "matches": len(rows),
            "results": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def get_chat(
    chat_id: str,
    base_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    conn = get_conn(base_dir)
    try:
        chat = conn.execute(
            "SELECT * FROM chats WHERE chat_id = ? OR url = ? OR title LIKE ?",
            (chat_id, chat_id, f"%{chat_id}%"),
        ).fetchone()
        if not chat:
            return None
        msgs = conn.execute(
            """
            SELECT turn_index, role, content, timestamp
            FROM messages
            WHERE chat_id = ?
            ORDER BY turn_index ASC
            """,
            (chat["chat_id"],),
        ).fetchall()
        result = dict(chat)
        result.pop("raw_json", None)
        result["messages"] = [dict(m) for m in msgs]
        return result
    finally:
        conn.close()
