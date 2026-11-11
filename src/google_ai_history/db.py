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
            first_seen_at TEXT,
            synced_at TEXT,
            source_chat_id TEXT,
            incomplete TEXT,
            message_count INTEGER DEFAULT 0,
            markdown_path TEXT,
            attachments_json TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT,
            kind TEXT,
            FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chats_fts USING fts5(
            chat_id UNINDEXED,
            title,
            full_text
        );
        """
    )
    _ensure_schema_columns(conn)
    return conn


def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
    """Safely adds newer columns to existing SQLite tables without losing data."""
    migrations: dict[str, dict[str, str]] = {
        "chats": {
            "first_seen_at": "TEXT",
            "synced_at": "TEXT",
            "source_chat_id": "TEXT",
            "incomplete": "TEXT",
            "attachments_json": "TEXT",
        },
        "messages": {
            "kind": "TEXT",
        },
    }
    for table, cols in migrations.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col_name, col_type in cols.items():
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


def slugify(text: str, max_len: int = 60) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip()).strip("_")
    return (cleaned[:max_len] or "untitled").lower()


def _cleanup_orphan_markdown_files(
    md_dir: Path,
    chat_id: str,
    keep_path: Path,
    previous_path: str | None,
) -> None:
    """Removes stale markdown files for chat_id when its filename/title changes."""
    candidates: set[Path] = set()
    if previous_path:
        candidates.add(Path(previous_path))
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", chat_id)
    if len(safe_id) >= 8:
        for p in md_dir.glob(f"*_{safe_id[-16:]}.md"):
            candidates.add(p)

    keep_resolved = keep_path.resolve()
    md_dir_resolved = md_dir.resolve()
    marker = f"- **Chat ID**: `{chat_id}`"
    for cand in candidates:
        try:
            cand_resolved = cand.resolve()
            if cand_resolved == keep_resolved or not cand_resolved.is_file():
                continue
            if cand_resolved.parent != md_dir_resolved:
                continue
            if previous_path and Path(previous_path).resolve() == cand_resolved:
                cand_resolved.unlink()
                continue
            head = cand_resolved.read_text(encoding="utf-8", errors="replace")[:512]
            if marker in head:
                cand_resolved.unlink()
        except OSError:
            pass


def upsert_chat(
    chat_id: str,
    title: str,
    messages: list[dict[str, Any]],
    source: str = "browser",
    url: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    base_dir: Path | str | None = None,
    source_chat_id: str | None = None,
    incomplete: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Saves or updates a chat in SQLite FTS5 and writes a local Markdown file."""
    paths = ensure_dirs(base_dir)
    now_iso = datetime.now(UTC).isoformat()
    msg_timestamps = [str(m["timestamp"]) for m in messages if m.get("timestamp")]
    resolved_created_at = created_at or (msg_timestamps[0] if msg_timestamps else None)
    resolved_updated_at = updated_at or (msg_timestamps[-1] if msg_timestamps else now_iso)
    title = (title or "Untitled Google AI Chat").strip()

    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", chat_id)
    md_filename = f"{slugify(title)}_{safe_id[-16:]}.md"
    md_path = paths["markdown"] / md_filename

    md_lines = [
        f"# {title}",
        "",
        f"- **Chat ID**: `{chat_id}`",
        f"- **Source**: `{source}`",
    ]
    if source_chat_id:
        md_lines.append(f"- **Source Chat ID**: `{source_chat_id}`")
    if url:
        md_lines.append(f"- **URL**: {url}")
    if resolved_created_at:
        md_lines.append(f"- **Created**: `{resolved_created_at}`")
    md_lines.extend(
        [
            f"- **Updated**: `{resolved_updated_at}`",
            f"- **Synced**: `{now_iso}`",
            f"- **Messages**: {len(messages)}",
        ]
    )
    if incomplete:
        md_lines.append(f"- **Incomplete**: `{incomplete}`")
    md_lines.extend(["", "---", ""])

    full_text_parts = [title]
    if attachments:
        md_lines.extend(["## Attachments", ""])
        for att in attachments:
            att_name = str(att.get("name") or att.get("filename") or "Attachment")
            drive_id = att.get("drive_id")
            att_url = att.get("url")
            detail = f"- **{att_name}**"
            if drive_id:
                detail += f" (Drive ID: `{drive_id}`)"
            if att_url:
                detail += f" — {att_url}"
            md_lines.append(detail)
            full_text_parts.append(detail)
            if att.get("content"):
                md_lines.extend(["", str(att["content"]).strip(), ""])
                full_text_parts.append(str(att["content"]).strip())
        md_lines.extend(["", "---", ""])

    for msg in messages:
        role = str(msg.get("role", "user")).upper()
        content = str(msg.get("content") or "").strip()
        ts = msg.get("timestamp")
        kind = msg.get("kind")
        header = f"### {role}"
        if kind:
            header += f" [{kind}]"
        if ts:
            header += f" ({ts})"
        md_lines.extend([header, "", content, "", "---", ""])
        full_text_parts.append(f"{role}: {content}")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    full_text = "\n\n".join(full_text_parts)
    attachments_json = json.dumps(attachments, ensure_ascii=False) if attachments else None

    conn = get_conn(base_dir)
    try:
        prev_row = conn.execute(
            "SELECT markdown_path, first_seen_at FROM chats WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        prev_md_path = (
            str(prev_row["markdown_path"]) if prev_row and prev_row["markdown_path"] else None
        )
        first_seen_at = (
            str(prev_row["first_seen_at"]) if prev_row and prev_row["first_seen_at"] else now_iso
        )
        _cleanup_orphan_markdown_files(paths["markdown"], chat_id, md_path, prev_md_path)

        with conn:
            conn.execute(
                """
                INSERT INTO chats (
                    chat_id, title, source, url, created_at, updated_at,
                    first_seen_at, synced_at, source_chat_id, incomplete,
                    message_count, markdown_path, attachments_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    source = excluded.source,
                    url = COALESCE(excluded.url, chats.url),
                    created_at = COALESCE(excluded.created_at, chats.created_at),
                    updated_at = excluded.updated_at,
                    first_seen_at = COALESCE(chats.first_seen_at, excluded.first_seen_at),
                    synced_at = excluded.synced_at,
                    source_chat_id = COALESCE(excluded.source_chat_id, chats.source_chat_id),
                    incomplete = excluded.incomplete,
                    message_count = excluded.message_count,
                    markdown_path = excluded.markdown_path,
                    attachments_json = COALESCE(excluded.attachments_json, chats.attachments_json),
                    raw_json = excluded.raw_json
                """,
                (
                    chat_id,
                    title,
                    source,
                    url,
                    resolved_created_at,
                    resolved_updated_at,
                    first_seen_at,
                    now_iso,
                    source_chat_id,
                    incomplete,
                    len(messages),
                    str(md_path),
                    attachments_json,
                    json.dumps(messages, ensure_ascii=False),
                ),
            )
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            for idx, msg in enumerate(messages):
                conn.execute(
                    """
                    INSERT INTO messages (chat_id, turn_index, role, content, timestamp, kind)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        idx,
                        str(msg.get("role", "user")),
                        str(msg.get("content") or "").strip(),
                        msg.get("timestamp"),
                        msg.get("kind"),
                    ),
                )
            conn.execute("DELETE FROM chats_fts WHERE chat_id = ?", (chat_id,))
            conn.execute(
                "INSERT INTO chats_fts (chat_id, title, full_text) VALUES (?, ?, ?)",
                (chat_id, title, full_text),
            )
    finally:
        conn.close()

    res: dict[str, Any] = {
        "chat_id": chat_id,
        "title": title,
        "message_count": len(messages),
        "markdown_path": str(md_path),
    }
    if incomplete:
        res["incomplete"] = incomplete
    if source_chat_id:
        res["source_chat_id"] = source_chat_id
    return res


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
            SELECT turn_index, role, content, timestamp, kind
            FROM messages
            WHERE chat_id = ?
            ORDER BY turn_index ASC
            """,
            (chat["chat_id"],),
        ).fetchall()
        result = dict(chat)
        result.pop("raw_json", None)
        att_raw = result.pop("attachments_json", None)
        if att_raw:
            try:
                result["attachments"] = json.loads(att_raw)
            except Exception:
                result["attachments"] = []
        result["messages"] = [dict(m) for m in msgs]
        return result
    finally:
        conn.close()
