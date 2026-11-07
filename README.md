# `gemini-web-chats-mcp`

[![CI](https://github.com/travispflanz/gemini-web-chats-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/travispflanz/gemini-web-chats-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An open-source **Model Context Protocol (MCP) server** and **CLI sync tool** that brings all your conversations across the **Google AI ecosystem** onto your local machine—indexed with **SQLite FTS5 full-text search** and saved as clean **Markdown files**—so you can search, reference, and `@`-mention them inside **Google Antigravity**, **Claude Code**, **Cursor**, or any MCP client.

---

## Supported Google AI Surfaces

| Google AI Surface | Where Chats Live | How `gemini-web-chats-mcp` Imports Them | `source` Tag |
| :--- | :--- | :--- | :--- |
| **Gemini Web App** (`gemini.google.com/app`) | Left sidebar threads & Takeout (`My Activity → Gemini Apps`) | Live Chrome CDP sidebar sync (`--target gemini`) + Takeout `.zip`/`.json`/`.html` | `browser_gemini`, `takeout_gemini` |
| **Google Search "AI Mode" & AI Overviews** (`google.com/search?udm=50`) | "Show more → Ask anything" AI Mode history drawer & Takeout (`My Activity → Search`) | Live Chrome CDP AI Mode sync (`--target aimode`) + Takeout `MyActivity.json` / `.html` | `browser_search_ai_mode`, `takeout_search_ai_mode` |
| **Google AI Studio** (`aistudio.google.com`) | Auto-saved as extensionless JSON files in `Google Drive/Google AI Studio/` & `aistudio.google.com/library` | Direct folder/file import (`chunkedPrompt` & `contents` JSON) + Live Chrome sync (`--target aistudio`) | `aistudio`, `browser_aistudio` |
| **Google Maps ("Ask Maps" / Gemini in Maps)** | Google Maps Activity (`My Activity → Maps`) | Takeout `MyActivity.json` / `MyActivity.html` importer | `takeout_ask_maps` |
| **YouTube "Ask" Video AI** | YouTube Activity (`My Activity → YouTube`) | Takeout `MyActivity.json` / `MyActivity.html` importer | `takeout_youtube_ai` |
| **NotebookLM** (`notebooklm.google.com`) | NotebookLM Takeout exports & Markdown/HTML notes | Takeout / folder importer | `notebooklm` |

---

## MCP Tools Provided

| Tool | Description |
| :--- | :--- |
| `list_gemini_web_chats(limit, offset, source)` | Lists synced/imported Google AI conversations stored locally (filterable by `source`). |
| `search_gemini_web_chats(query, limit)` | Runs a SQLite FTS5 full-text search across all prompts and responses. |
| `get_gemini_web_chat(chat_id)` | Returns the full multi-turn conversation transcript and local `.md` file path. |
| `sync_gemini_chats_from_browser(target, max_chats, login_timeout_sec)` | Attaches to Chrome and syncs threads from `'gemini'`, `'aimode'`, `'aistudio'`, or `'all'`. |
| `import_gemini_takeout_or_files(path)` | Imports a Google Takeout `.zip`/`.json`/`.html`, a `Google AI Studio` Drive folder, or JSON/MD exports. |

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
# Sync all browser surfaces (Gemini + Google Search AI Mode + AI Studio)
uv run gemini-web-chats sync --target all --max-chats 50

# Sync only Google Search AI Mode ("Show more -> Ask anything" threads)
uv run gemini-web-chats sync --target aimode

# Sync only Google AI Studio saved prompts
uv run gemini-web-chats sync --target aistudio

# Import a Google Takeout archive (.zip, MyActivity.json, or MyActivity.html)
uv run gemini-web-chats import ~/Downloads/takeout.zip

# Import your Google Drive "Google AI Studio" folder
uv run gemini-web-chats import "~/Google Drive/My Drive/Google AI Studio"

# Search across all synced chats
uv run gemini-web-chats search "kubernetes ingress"

# List chats filtered by source
uv run gemini-web-chats list --source takeout_search_ai_mode
```

---

## Development & CI

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=gemini_web_chats --cov-report=term-missing --cov-fail-under=85
```
