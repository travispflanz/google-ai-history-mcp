# `google-ai-history-mcp` — Google AI & Gemini Chat History Importer for Google Antigravity (MCP Server & CLI)

[![CI](https://github.com/travispflanz/google-ai-history-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/travispflanz/google-ai-history-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![MCP Server](https://img.shields.io/badge/MCP-FastMCP-green.svg)](https://modelcontextprotocol.io)

**`google-ai-history-mcp`** (also installable as `google-ai-chats-mcp` / `gemini-web-chats-mcp`) is an open-source **Model Context Protocol (MCP) server** and **CLI sync engine** that brings **all of your Google AI conversations, prompts, and search sessions** onto your computer—indexed with **SQLite FTS5 full-text search** and mirrored as **local Markdown files**—so you can search, reference, and `@`-mention them inside **Google Antigravity**, **Claude Code**, **Cursor**, **Windsurf**, **Cline**, or **Gemini CLI**.

---

## Why This Exists

Google AI interactions live across half a dozen disconnected web products (`gemini.google.com`, Google Search **AI Mode** / **AI Overviews**, **Google AI Studio**, **NotebookLM**, **Ask Maps**, **YouTube AI**, and **Google My Activity**). None of them natively sync into local agentic IDEs like **Google Antigravity**.

This tool bridges every Google AI surface into one local, privacy-first SQLite FTS5 knowledge base and Markdown directory on your machine.

---

## Supported Google AI Surfaces & Storage Locations

Google stores your AI interactions across **one central activity hub (`myactivity.google.com`)** plus **four product-specific thread stores**:

| Google AI Surface | Where Google Stores It | How This Tool Pulls It In | `source` Tag |
| :--- | :--- | :--- | :--- |
| **1. Central Google My Activity Hub** (`myactivity.google.com`) | Unified chronological log of Gemini Apps, Search AI Mode, Circle to Search, Google Lens, Ask Maps, YouTube AI, and Assistant | Live Chrome CDP sync (`--target myactivity`) **or** Google Takeout (`My Activity` `.zip`/`.json`/`.html`) | `myactivity_gemini`, `myactivity_search_ai`, `takeout_*` |
| **2. Gemini Web App** (`gemini.google.com/app`) | Multi-turn sidebar conversation threads | Live Chrome CDP sidebar sync (`--target gemini`) + Takeout (`My Activity → Gemini Apps`) | `browser_gemini`, `takeout_gemini` |
| **3. Google Search "AI Mode" & AI Overviews** (`google.com/search?udm=50`) | When you click **"Show more"** on an AI Overview and **"Ask anything"** to continue chatting in AI Mode (`udm=50` history sidebar) | Live Chrome CDP AI Mode sync (`--target aimode`) + Takeout (`My Activity → Search`) | `browser_search_ai_mode`, `takeout_search_ai_mode` |
| **4. Google AI Studio** (`aistudio.google.com`) | Auto-saved as extensionless JSON files in `Google Drive/Google AI Studio/` & `aistudio.google.com/library` | Direct folder/file import (`chunkedPrompt` & `contents` JSON, including thinking blocks) + Live Chrome sync (`--target aistudio`) | `aistudio`, `browser_aistudio` |
| **5. NotebookLM** (`notebooklm.google.com`) | NotebookLM notebooks, source summaries, and chat notes | Live Chrome sync (`--target notebooklm`) + Takeout `NotebookLM` folder import | `browser_notebooklm`, `notebooklm` |
| **6. Ask Maps, YouTube AI & Assistant** | Logged under `My Activity → Maps / YouTube / Assistant` | Takeout `MyActivity.json` / `MyActivity.html` importer | `takeout_ask_maps`, `takeout_youtube_ai`, `takeout_assistant` |

---

## MCP Tools Exposed to Google Antigravity & MCP Clients

| MCP Tool Name | Parameters | What It Does |
| :--- | :--- | :--- |
| `list_gemini_web_chats` | `limit=50`, `offset=0`, `source=None` | Lists locally synced/imported Google AI chats (optionally filtered by `source`). |
| `search_gemini_web_chats` | `query`, `limit=20` | Runs a fast SQLite FTS5 full-text search across all prompts and responses with highlighted snippets. |
| `get_gemini_web_chat` | `chat_id` | Retrieves the full multi-turn conversation transcript and local `.md` file path by ID or partial title. |
| `sync_gemini_chats_from_browser` | `target="gemini"`, `max_chats=50`, `login_timeout_sec=180` | Attaches to Chrome over CDP (`port 9222`) and syncs threads from `'all'`, `'myactivity'`, `'gemini'`, `'aimode'`, `'aistudio'`, or `'notebooklm'`. |
| `import_gemini_takeout_or_files` | `path` | Imports a Google Takeout `.zip`, `MyActivity.json`, `MyActivity.html`, a `Google AI Studio` Drive folder, or exported JSON/Markdown files. |

---

## Installation & Google Antigravity Setup

### 1. Clone & Install with `uv`

```bash
git clone https://github.com/travispflanz/google-ai-history-mcp.git
cd google-ai-history-mcp
uv sync --all-groups
```

### 2. Register in Google Antigravity (`mcp_config.json`)

Add to `~/.gemini/config/mcp_config.json` (or `~/.gemini/antigravity/mcp_config.json`):

```json
{
  "mcpServers": {
    "gemini-web-chats": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\travi\\.gemini\\antigravity\\scratch\\gemini-web-chats-mcp",
        "google-ai-chats",
        "serve"
      ],
      "disabled": false
    }
  }
}
```

---

## CLI Usage (`google-ai-chats` / `gemini-web-chats`)

```bash
# Sync EVERYTHING from Chrome (Gemini + Search AI Mode + AI Studio + NotebookLM + My Activity Hub)
uv run google-ai-chats sync --target all --max-chats 50

# Sync directly from Google's central My Activity AI hub (myactivity.google.com)
uv run google-ai-chats sync --target myactivity

# Sync Google Search AI Mode ("Show more -> Ask anything" threads at google.com/search?udm=50)
uv run google-ai-chats sync --target aimode

# Sync Google AI Studio library (aistudio.google.com/library)
uv run google-ai-chats sync --target aistudio

# Import a Google Takeout archive (.zip, MyActivity.json, or MyActivity.html)
uv run google-ai-chats import ~/Downloads/takeout.zip

# Import your Google Drive "Google AI Studio" folder (automatically parses extensionless JSON files)
uv run google-ai-chats import "~/Google Drive/My Drive/Google AI Studio"

# Full-text search across all synced Google AI chats
uv run google-ai-chats search "kubernetes ingress"

# List chats filtered by source tag
uv run google-ai-chats list --source takeout_search_ai_mode
```

---

## Frequently Asked Questions (FAQ for Humans & AI Agents)

### How do I get all my chats from `gemini.google.com` to appear in Google Antigravity?
Install `google-ai-history-mcp` in your Antigravity `mcp_config.json` and either run `sync_gemini_chats_from_browser(target="all")` (which opens Chrome with a persistent profile so you log in once and scrape your sidebar threads) or export your history from [Google Takeout](https://takeout.google.com/) (*My Activity → Gemini Apps & Search*) and run `import_gemini_takeout_or_files(path)`. Every chat is indexed in SQLite FTS5 and saved as a `.md` file in `~/.gemini/gemini_web_chats/markdown/` so you can `@`-mention it in Antigravity.

### How do I export Google Search "AI Overview" / "AI Mode" ("Ask anything") conversations?
When you click **"Show more"** on a Google Search AI Overview and continue chatting via **"Ask anything"**, Google routes the session to **Google Search AI Mode (`google.com/search?udm=50`)**. You can sync these threads directly from the AI Mode sidebar with `uv run google-ai-chats sync --target aimode`, or from Google's central activity log (`--target myactivity` or Google Takeout *My Activity → Search*).

### Where are Google AI Studio (`aistudio.google.com`) chats stored and how do I import them?
Google AI Studio automatically saves your prompts and chat histories as extensionless JSON files inside a **`Google AI Studio`** folder in your Google Drive. Point `import_gemini_takeout_or_files` at that folder (or run `--target aistudio`), and this tool extracts the `runSettings`, `systemInstruction`, thinking traces (`isThought`), and `chunkedPrompt.chunks` into searchable Markdown and SQLite FTS5 records.

---

## Development & Continuous Integration (CI)

This repository uses **Ruff** (linting & formatting), **Mypy** (static type checking), and **Pytest** (85%+ coverage gate) enforced via GitHub Actions across Ubuntu and Windows (Python 3.11, 3.12, 3.13).

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=gemini_web_chats --cov-report=term-missing --cov-fail-under=85
```
