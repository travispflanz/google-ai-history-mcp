# `google-ai-history-mcp` — Google AI & Gemini Chat History Importer for Google Antigravity (MCP Server & CLI)

[![CI](https://github.com/travispflanz/google-ai-history-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/travispflanz/google-ai-history-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![MCP Server](https://img.shields.io/badge/MCP-FastMCP-green.svg)](https://modelcontextprotocol.io)

**`google-ai-history-mcp`** is an open-source **Model Context Protocol (MCP) server** and **CLI sync engine** that brings **all of your Google AI conversations, prompts, and search sessions** onto your computer—indexed with **SQLite FTS5 full-text search**, mirrored as **local Markdown files**, and accessible by **dropping any Google AI URL directly into chat**—so you can continue sessions and build real projects from them inside **Google Antigravity**, **Claude Code**, **Cursor**, **Windsurf**, **Cline**, or **Gemini CLI**.

---

## Why This Exists & How It Changes Your Workflow

### 1. Drop a URL & Say *"Continue This Chat"* or *"Build a Project From This"*
You don't even have to run a full sync first. You can paste a URL from **`gemini.google.com/app/...`**, a shared link (**`gemini.google.com/share/...`**), a **Google Search AI Mode** session (**`google.com/search?udm=50...`**), a **Google AI Studio** prompt (**`aistudio.google.com/prompts/...`**), or a **NotebookLM** notebook directly into **Google Antigravity** and say:
> - *"I want to continue this chat: `https://gemini.google.com/app/...`"*
> - *"Build a project from this session: `https://gemini.google.com/app/...`"*
> - *"Start a new project in my workspace from this chat line: `https://aistudio.google.com/prompts/...`"*

Antigravity automatically calls `fetch_google_ai_chat_url`, pulls every prompt, response, code block, and thinking trace from that URL, saves it locally as Markdown + SQLite FTS5, and picks up right where you left off.

### 2. Graduate a "Small One-Off Question" into a Full Project
Some of the best projects start by accident as a quick one-off interaction in the browser:
- You do a Google Search, click **"Show more"** on the **AI Overview**, and start asking follow-up questions in **"Ask anything" (AI Mode)**.
- You brainstorm an app idea or debug a script in **`gemini.google.com`** on your phone or laptop.
- You prototype a system prompt in **Google AI Studio (`aistudio.google.com`)**.

When that "small one-off chat" turns into something bigger that needs real files, a Git repository, terminal execution, and autonomous subagents, you no longer have to copy-paste 30 turns of chat history or start from scratch. Just drop the URL (or search your synced history by keyword) in **Google Antigravity** and roll that conversation directly into a full engineering workspace.

### 3. New to Google Antigravity? Bring Your Entire Google AI History With You
When you start using **Google Antigravity**, months or years of your past conversations, architectural decisions, prompts, and research shouldn't stay trapped across disconnected web tabs (`gemini.google.com`, Google Search AI Mode, AI Studio, NotebookLM, Ask Maps, and YouTube AI). `google-ai-history-mcp` unifies all of your past Google AI interactions into a local, privacy-first knowledge base on your machine from day one.

---

## Supported Google AI Surfaces & Storage Locations

Google stores your AI interactions across **one central activity hub (`myactivity.google.com`)** plus **four product-specific thread stores**:

| Google AI Surface | Where Google Stores It | How `google-ai-history-mcp` Pulls It In | `source` Tag |
| :--- | :--- | :--- | :--- |
| **1. Direct URL Drop (Any Surface)** | Live web session (`gemini.google.com/app/...`, `/share/...`, `udm=50`, `aistudio.google.com/prompts/...`, `notebooklm.google.com/notebook/...`) | Instant on-demand extraction via `fetch_google_ai_chat_url(url)` or `google-ai-history fetch <url>` | `browser_gemini`, `url_gemini_share`, `browser_aistudio`, etc. |
| **2. Central Google My Activity Hub** (`myactivity.google.com`) | Unified chronological log of Gemini Apps, Search AI Mode, Circle to Search, Google Lens, Ask Maps, YouTube AI, and Assistant | Live Chrome CDP sync (`--target myactivity`) **or** Google Takeout (`My Activity` `.zip`/`.json`/`.html`) | `myactivity_gemini`, `myactivity_search_ai`, `takeout_*` |
| **3. Gemini Web App** (`gemini.google.com/app`) | Multi-turn sidebar conversation threads | Live Chrome CDP sidebar sync (`--target gemini`) + Takeout (`My Activity → Gemini Apps`) | `browser_gemini`, `takeout_gemini` |
| **4. Google Search "AI Mode" & AI Overviews** (`google.com/search?udm=50`) | When you click **"Show more"** on an AI Overview and **"Ask anything"** to continue chatting in AI Mode (`udm=50` history sidebar) | Live Chrome CDP AI Mode sync (`--target aimode`) + Takeout (`My Activity → Search`) | `browser_search_ai_mode`, `takeout_search_ai_mode` |
| **5. Google AI Studio** (`aistudio.google.com`) | Auto-saved as extensionless JSON files in `Google Drive/Google AI Studio/` & `aistudio.google.com/library` | Direct folder/file import (`chunkedPrompt` & `contents` JSON, including thinking blocks) + Live Chrome sync (`--target aistudio`) | `aistudio`, `browser_aistudio` |
| **6. NotebookLM** (`notebooklm.google.com`) | NotebookLM notebooks, source summaries, and chat notes | Live Chrome sync (`--target notebooklm`) + Takeout `NotebookLM` folder import | `browser_notebooklm`, `notebooklm` |
| **7. Ask Maps, YouTube AI & Assistant** | Logged under `My Activity → Maps / YouTube / Assistant` | Takeout `MyActivity.json` / `MyActivity.html` importer | `takeout_ask_maps`, `takeout_youtube_ai`, `takeout_assistant` |

---

## MCP Tools Exposed to Google Antigravity & MCP Clients

| MCP Tool Name | Parameters | What It Does |
| :--- | :--- | :--- |
| `fetch_google_ai_chat_url` | `url`, `login_timeout_sec=45` | **Drop-a-URL tool**: Pulls any single Google AI chat/session directly from its URL, saves it locally, and returns the full transcript so you can continue the chat or build a project from it. |
| `get_google_ai_chat` | `chat_id`, `login_timeout_sec=45` | Retrieves the full multi-turn conversation transcript and local `.md` file path by ID, partial title, or URL (automatically fetches live if the URL isn't cached yet). |
| `search_google_ai_chats` | `query`, `limit=20` | Runs a fast SQLite FTS5 full-text search across all prompts and responses with highlighted snippets. |
| `list_google_ai_chats` | `limit=50`, `offset=0`, `source=None` | Lists locally synced/imported Google AI chats (optionally filtered by `source`). |
| `sync_google_ai_chats_from_browser` | `target="gemini"`, `max_chats=50`, `login_timeout_sec=180` | Attaches to Chrome over CDP (`port 9222`) and bulk-syncs threads from `'all'`, `'myactivity'`, `'gemini'`, `'aimode'`, `'aistudio'`, or `'notebooklm'`. |
| `import_google_ai_takeout_or_files` | `path` | Imports a Google Takeout `.zip`, `MyActivity.json`, `MyActivity.html`, a `Google AI Studio` Drive folder, or exported JSON/Markdown files. |

---

## System Requirements & Expected First-Run Process (For Humans & AI Agents)

### Additional Requirements (Beyond Google Antigravity)
To run `google-ai-history-mcp` on your computer, you need:
1. **Google Chrome** or **Microsoft Edge** installed locally (used via Playwright over Chrome DevTools Protocol on `127.0.0.1:9222` for live URL fetching and browser syncing; not required for offline Takeout/Drive imports).
2. **`uv`** ([Astral uv](https://docs.astral.sh/uv/)) and **Python 3.11+ (`3.11`, `3.12`, or `3.13`)**.
3. **A Google Account** signed into your Google AI surfaces (`gemini.google.com`, `myactivity.google.com`, `aistudio.google.com`, etc.) — *except when importing public `gemini.google.com/share/...` links or offline Takeout files, which require no login.*

### Expected Process: How First-Run Sign-In & Persistent Sync Works
Even if you are already signed into Google in your normal everyday Chrome window, **Google Chrome v136+ blocks external debugging connections (`--remote-debugging-port`) on your default Chrome profile** while Chrome is open. To keep your main browser safe and untouched, `google-ai-history-mcp` manages a dedicated, persistent Chrome sync profile at `~/.gemini/google_ai_history/chrome_profile`:

1. **First Run (One-Time Google Sign-In)**:
   - The first time you ask Antigravity to import a URL or sync your history, `google-ai-history-mcp` launches a visible Chrome window on your desktop (`--remote-debugging-port=9222 --user-data-dir=~/.gemini/google_ai_history/chrome_profile`) and automatically clicks the **"Sign in"** button if you aren't logged into that profile yet.
   - Sign into your Google account **once** in that Chrome window.
   - As soon as sign-in finishes, `google-ai-history-mcp` automatically navigates back to your exact target URL (e.g., `https://gemini.google.com/app/<id>`), waits for the conversation turns to render, extracts the full chat, writes the Markdown file to `~/.gemini/google_ai_history/markdown/`, and indexes it in `~/.gemini/google_ai_history/chats.db`.
2. **All Subsequent Runs (Zero Sign-In Needed)**:
   - Because your login cookies are persisted inside `~/.gemini/google_ai_history/chrome_profile`, every future URL drop or sync reuses that authenticated session automatically without asking you to log in again.
3. **Critical Execution Rule for AI Agents (Antigravity, Claude Code, Cursor)**:
   - **Always invoke the MCP tools (`fetch_google_ai_chat_url`, `get_google_ai_chat`, `sync_google_ai_chats_from_browser`) directly** instead of running the CLI via a sandboxed shell command (`run_command`).
   - *Why?* On Windows, agent shell sandboxes often execute inside an isolated background desktop (`WinSta0\exebox-...`), which hides any GUI browser window spawned from the shell. By contrast, the MCP server process runs on the user's interactive desktop (`WinSta0\Default`), ensuring the Chrome window is always visible when a one-time login is needed.

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
    "google-ai-history": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\travi\\.gemini\\antigravity\\scratch\\google-ai-history-mcp",
        "google-ai-history",
        "serve"
      ],
      "disabled": false
    }
  }
}
```

---

## CLI Usage (`google-ai-history`)

```bash
# Fetch a single Google AI chat/session directly by URL
uv run google-ai-history fetch "https://gemini.google.com/app/1a2b3c4d5e6f"

# Sync EVERYTHING from Chrome (Gemini + Search AI Mode + AI Studio + NotebookLM + My Activity Hub)
uv run google-ai-history sync --target all --max-chats 50

# Sync directly from Google's central My Activity AI hub (myactivity.google.com)
uv run google-ai-history sync --target myactivity

# Sync Google Search AI Mode ("Show more -> Ask anything" threads at google.com/search?udm=50)
uv run google-ai-history sync --target aimode

# Sync Google AI Studio library (aistudio.google.com/library)
uv run google-ai-history sync --target aistudio

# Import a Google Takeout archive (.zip, MyActivity.json, or MyActivity.html)
uv run google-ai-history import ~/Downloads/takeout.zip

# Import your Google Drive "Google AI Studio" folder (automatically parses extensionless JSON files)
uv run google-ai-history import "~/Google Drive/My Drive/Google AI Studio"

# Full-text search across all synced Google AI chats
uv run google-ai-history search "kubernetes ingress"

# List chats filtered by source tag
uv run google-ai-history list --source takeout_search_ai_mode
```

---

## Frequently Asked Questions (FAQ for Humans & AI Agents)

### Can I just drop a URL from a Gemini chat into Antigravity and say "Continue this chat" or "Build a project from this"?
Yes! Paste any URL from `gemini.google.com/app/...`, `gemini.google.com/share/...`, `aistudio.google.com/prompts/...`, `google.com/search?udm=50...`, or `notebooklm.google.com/notebook/...` into Antigravity and ask to continue the session or build a project from it. Antigravity will call `fetch_google_ai_chat_url` (or `get_google_ai_chat`) to pull the entire conversation into your workspace immediately.

### How do I get all my chats from `gemini.google.com` to appear in Google Antigravity?
Install `google-ai-history-mcp` in your Antigravity `mcp_config.json` and either run `sync_google_ai_chats_from_browser(target="all")` (which opens Chrome with a persistent profile so you log in once and scrape your sidebar threads) or export your history from [Google Takeout](https://takeout.google.com/) (*My Activity → Gemini Apps & Search*) and run `import_google_ai_takeout_or_files(path)`. Every chat is indexed in SQLite FTS5 and saved as a `.md` file in `~/.gemini/google_ai_history/markdown/` so you can `@`-mention it in Antigravity.

### How do I export Google Search "AI Overview" / "AI Mode" ("Ask anything") conversations?
When you click **"Show more"** on a Google Search AI Overview and continue chatting via **"Ask anything"**, Google routes the session to **Google Search AI Mode (`google.com/search?udm=50`)**. You can drop the URL directly into Antigravity, sync all threads from the AI Mode sidebar with `uv run google-ai-history sync --target aimode`, or pull from Google's central activity log (`--target myactivity` or Google Takeout *My Activity → Search*).

### Where are Google AI Studio (`aistudio.google.com`) chats stored and how do I import them?
Google AI Studio automatically saves your prompts and chat histories as extensionless JSON files inside a **`Google AI Studio`** folder in your Google Drive. Point `import_google_ai_takeout_or_files` at that folder (or drop an `aistudio.google.com/prompts/...` URL / run `--target aistudio`), and `google-ai-history-mcp` extracts the `runSettings`, `systemInstruction`, thinking traces (`isThought`), and `chunkedPrompt.chunks` into searchable Markdown and SQLite FTS5 records.

---

## Development & Continuous Integration (CI)

This repository uses **Ruff** (linting & formatting), **Mypy** (static type checking), and **Pytest** (85%+ coverage gate) enforced via GitHub Actions across Ubuntu and Windows (Python 3.11, 3.12, 3.13).

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=google_ai_history --cov-report=term-missing --cov-fail-under=85
```
