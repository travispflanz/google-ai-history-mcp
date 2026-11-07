# `gemini-web-chats-mcp`

[![CI](https://github.com/travispflanz/gemini-web-chats-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/travispflanz/gemini-web-chats-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An open-source **Model Context Protocol (MCP) server** and **CLI sync tool** that brings all your conversations from **[gemini.google.com](https://gemini.google.com)** onto your local machine—indexed with **SQLite FTS5 full-text search** and saved as clean **Markdown files**—so you can search, reference, and `@`-mention them inside **Google Antigravity**, **Claude Code**, **Cursor**, or any MCP client.

---

## Features

- **Live Browser Sidebar Sync (`sync_gemini_chats_from_browser`)**: Connects to Chrome over CDP (`--remote-debugging-port=9222`) with a persistent local profile, scrolls your `gemini.google.com` sidebar, and extracts full conversation threads into SQLite FTS5 + Markdown.
- **Google Takeout Importer (`import_gemini_takeout_or_files`)**: Parses Google Takeout exports (`.zip`, `MyActivity.json`, or `MyActivity.html` from *My Activity → Gemini Apps*) and automatically groups chronological prompt/response pairs into coherent conversation threads.
- **Extension JSON / Markdown Folder Importer**: Ingests JSON or Markdown exports from browser extensions (`gemini-exporter`, `ai-chat-exporter`, etc.).
- **Instant SQLite FTS5 Search (`search_gemini_web_chats`)**: Millisecond full-text search with highlighted snippets across thousands of past chats.
- **Local Markdown Mirror**: Every imported or synced chat is written to `~/.gemini/gemini_web_chats/markdown/<title>_<id>.md` for direct `@`-mentioning in Antigravity.

---

## MCP Tools Provided

| Tool | Description |
| :--- | :--- |
| `list_gemini_web_chats(limit, offset, source)` | Lists synced/imported `gemini.google.com` conversations stored locally. |
| `search_gemini_web_chats(query, limit)` | Runs a SQLite FTS5 full-text search across all prompts and responses. |
| `get_gemini_web_chat(chat_id)` | Returns the full multi-turn conversation transcript and local `.md` file path. |
| `sync_gemini_chats_from_browser(max_chats, login_timeout_sec)` | Attaches to Chrome, scrolls your `gemini.google.com` sidebar, and syncs threads locally. |
| `import_gemini_takeout_or_files(path)` | Imports a Google Takeout `.zip`, `MyActivity.json`, `MyActivity.html`, or exported JSON/MD directory. |

---

## Quickstart & Installation

### 1. Clone & Install with `uv`

```bash
git clone https://github.com/travispflanz/gemini-web-chats-mcp.git
cd gemini-web-chats-mcp
uv sync --all-groups
```

### 2. Add to Google Antigravity (`~/.gemini/antigravity/mcp_config.json` or `~/.gemini/config/mcp_config.json`)

```json
{
  "mcpServers": {
    "gemini-web-chats": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\travi\\.gemini\\antigravity\\scratch\\gemini-web-chats-mcp",
        "gemini-web-chats",
        "serve"
      ],
      "disabled": false
    }
  }
}
```

---

## CLI Usage

```bash
# Sync your sidebar chats directly from gemini.google.com via Chrome
uv run gemini-web-chats sync --max-chats 100

# Import a Google Takeout archive (.zip, MyActivity.json, or MyActivity.html)
uv run gemini-web-chats import ~/Downloads/takeout.zip

# Search across all synced chats
uv run gemini-web-chats search "kubernetes ingress"

# List all locally indexed chats
uv run gemini-web-chats list
```

---

## Development & CI

This repository uses **Ruff** (linting & formatting), **Mypy** (static type checking), and **Pytest** (unit & integration tests with coverage) enforced via GitHub Actions across Linux and Windows.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=gemini_web_chats --cov-report=term-missing
```
