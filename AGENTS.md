# AGENTS.md — AI Agent Self-Bootstrap, Auto-Install & User Walkthrough Playbook

This file provides step-by-step instructions for **Google Antigravity**, **Claude Code**, **Cursor**, **Windsurf**, **Cline**, and other AI coding agents to automatically detect missing prerequisites, install `google-ai-history-mcp`, register it in the user's AI harness, and walk the human user through any interactive steps.

---

## 1. Prerequisite Detection & Auto-Remediation Checklist

Before running or registering `google-ai-history-mcp`, check the user's environment for the following necessities and install any that are missing:

### A. Check for `uv` (Astral Python Package Manager)
- **Check**: `uv --version`
- **If missing, install automatically**:
  - **Windows (PowerShell)**:
    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```
    *(Or via Winget: `winget install --id=astral-sh.uv -e`)*
  - **macOS / Linux**:
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
- **Ensure Python 3.11+ is available**:
  - `uv` automatically downloads and manages Python `3.11+` (`3.12` recommended) during `uv sync` if system Python is missing (`uv python install 3.12`).

### B. Check for Google Chrome or Microsoft Edge
- **Check**:
  - **Windows**: Check `C:\Program Files\Google\Chrome\Application\chrome.exe`, `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`, or `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`.
  - **macOS**: Check `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` or `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`.
  - **Linux**: Check `which google-chrome || which google-chrome-stable || which chromium || which microsoft-edge`.
- **If missing**:
  - Attempt automated install (`winget install Google.Chrome`, `brew install --cask google-chrome`, or `sudo apt-get install -y chromium-browser`), **OR** inform the user:
    > *"Live URL fetching and browser syncing require Google Chrome or Microsoft Edge. Would you like me to install Chrome for you, or would you prefer to import an offline Google Takeout archive / Google Drive 'Google AI Studio' folder instead?"*

---

## 2. Automated Repository Clone & Dependency Sync

1. **Choose a persistent directory** inside the user's home or harness folder:
   - **Google Antigravity (Windows)**: `C:\Users\<user>\.gemini\antigravity\projects\google-ai-history-mcp`
   - **macOS / Linux / Generic MCP**: `~/.gemini/antigravity/projects/google-ai-history-mcp` or `~/.mcp/google-ai-history-mcp`
2. **Clone, install, and run `doctor` health check**:
   ```bash
   git clone https://github.com/travispflanz/google-ai-history-mcp.git
   cd google-ai-history-mcp
   uv sync --all-groups
   uv tool install --editable . --force
   google-ai-history doctor
   ```
   - **Note on Public Share Links (`share.gemini.google/...`, `gemini.google.com/share/...`, `g.co/gemini/share/...`)**: Public Gemini share links use a direct HTTP + RPC fast-path (`_fetch_public_share_via_http`) that extracts the full native Markdown conversation in **~1 second without Chrome or Google authentication**. In headless environments (like Hermes Agent, WSL, or Linux containers), `google-ai-history fetch <share_url>` works immediately out of the box!

---

## 3. Automated Harness Registration (MCP Config + Agent Skill)

### A. Register the MCP Server in `mcp_config.json`
Read (or create) the user's harness MCP configuration file and merge the `"google-ai-history"` entry into `"mcpServers"` (do not overwrite existing MCP servers):

- **Google Antigravity**:
  - `~/.gemini/config/mcp_config.json`
  - `~/.gemini/antigravity/mcp_config.json`
- **Cursor**: `~/.cursor/mcp.json`
- **Claude Desktop / Claude Code**: `claude_desktop_config.json` or `~/.claude.json`

**Recommended Configuration (Reliable Across Windows, macOS & Linux)**:
```json
{
  "mcpServers": {
    "google-ai-history": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/google-ai-history-mcp",
        "google-ai-history",
        "serve"
      ],
      "disabled": false
    }
  }
}
```
*(On Windows, prefer `"command": "uv"` with `"args": ["run", "--directory", "<repo-path>", "google-ai-history", "serve"]` or the full `.venv\Scripts\google-ai-history.exe` path — bare `"google-ai-history"` without `.exe` / `PATHEXT` resolution can return `exit status 0xffffffff` in Antigravity. On macOS/Linux, global `google-ai-history` from `uv tool install --editable . --force` also works cleanly.)*

### B. Install the Global Antigravity Skill (`SKILL.md`)
For Google Antigravity users, also create `~/.gemini/config/skills/google-ai-history/SKILL.md` so future agent conversations automatically know when and how to invoke the `google-ai-history` MCP tools whenever the user drops a Google AI URL or asks about past chats.

---

## 4. How the AI Agent Must Walk the User Through Setup & Usage

When setting up or running `google-ai-history-mcp` for a user, follow this exact conversational walkthrough protocol:

### Step 1: Prompt the User to Refresh the MCP Server (If Newly Installed or Updated)
If `google-ai-history` (or `fetch_google_ai_chat_url`) does not appear in your active MCP tool list yet after editing `mcp_config.json`:
- **Tell the user**:
  > *"I have installed and registered `google-ai-history` in your MCP configuration. Please open the **MCP Servers** panel in Antigravity, click **Refresh / Restart** next to **`google-ai-history`**, and reply **'ready'** so I can call the tool."*

### Step 2: Explain the One-Time Chrome Sign-In Window Before First Use
Before calling `fetch_google_ai_chat_url` or `sync_google_ai_chats_from_browser` for the very first time (when `~/.gemini/google_ai_history/chrome_profile` has not been authenticated yet):
- **Tell the user upfront**:
  > *"Because Chrome v136+ blocks remote debugging on your main Chrome browser profile, a dedicated Chrome window is about to open on your screen. Please sign into your Google account **once** in that window—as soon as you sign in, I will automatically navigate to your chat, import it into Antigravity, and save your session so you won't have to sign in again."*

### Step 3: Always Use `call_mcp_tool` (Never `run_command`) for Browser Tools on Windows
- **CRITICAL**: Never invoke `google-ai-history fetch` or `google-ai-history sync` via a sandboxed shell command (`run_command`) on Windows.
- **Why**: Sandboxed shell tools on Windows run inside an isolated background desktop (`WinSta0\exebox-...`), which hides the Chrome window from the user's screen. Calling the MCP tools (`fetch_google_ai_chat_url`, `get_google_ai_chat`, `sync_google_ai_chats_from_browser`) executes inside the MCP server on the user's interactive desktop (`WinSta0\Default`) so the Chrome window is visible.

### Step 4: Offline Fallback Walkthrough (If Browser Sync Is Unavailable)
If the user is on a headless machine, cannot use Chrome CDP, or wants to import years of history (including **Ask Maps**, **YouTube AI**, and **Google Assistant**):
1. **For Google AI Studio**: Ask if they have Google Drive for Desktop installed, locate their `Google AI Studio` folder, and call `import_google_ai_takeout_or_files(path)`.
2. **For Gemini, Search AI Mode, Ask Maps & YouTube AI**: Walk the user through exporting from [Google Takeout](https://takeout.google.com/):
   - Click **Deselect all**.
   - Check **My Activity** (select **JSON** format under *Multiple formats*, and under *All activity data included* select **Gemini Apps**, **Search**, **Maps**, and **YouTube**) and/or **NotebookLM**.
   - Once downloaded, pass the `.zip` path to `import_google_ai_takeout_or_files(path)`.

---

## 5. Automated Issue & PR Reporting When AI Agents Discover Bugs

If you (an AI agent) encounter an unexpected bug, missing dependency, edge case, or Google AI page DOM change while installing or running `google-ai-history-mcp` (and especially if you had to write a workaround during your session):

1. **Sanitize First (Strict Privacy Rule)**:
   - **NEVER** include the user's private chat text, personal email address, or private `gemini.google.com/app/<id>` session IDs in any issue or PR description.
   - Include only: your agent harness name & OS/Python version, sanitized output of `google-ai-history doctor`, the exact traceback or selector change, and the fix/workaround you identified.
2. **Check if the User Is Set Up with GitHub CLI (`gh auth status`)**:
   - **If `gh auth status` succeeds (GitHub CLI is authenticated)**:
     - Ask the user for confirmation first (or submit immediately if the user pre-authorized automatic issue filing):
       > *"I encountered an issue in `google-ai-history-mcp`. Since your GitHub CLI (`gh`) is authenticated, would you like me to submit a sanitized `[Agent Report]` GitHub Issue to `travispflanz/google-ai-history-mcp`?"*
     - Upon approval, submit with the `agent-report` + `bug` labels using a uniform structure so automated triage can parse it cleanly:
       ```bash
       gh issue create --repo travispflanz/google-ai-history-mcp \
         --label "bug,agent-report" \
         --title "[Agent Report]: <concise summary of issue>" \
         --body "## Environment\n- Agent Harness: <name>\n- OS / Python: <os / python>\n- Commit: <git rev-parse --short HEAD>\n\n## Command / Tool & Expected vs Actual\n...\n\n## Sanitized Doctor / Traceback\n...\n\n## Suggested Fix / Workaround\n..."
       ```
   - **If `gh` is NOT installed or NOT authenticated**:
     - Do **not** force the user to install or log into GitHub. Simply include a brief note at the end of your response with a copy-pasteable summary and a pre-filled 1-click URL to [`https://github.com/travispflanz/google-ai-history-mcp/issues/new`](https://github.com/travispflanz/google-ai-history-mcp/issues/new) in case they want to report it.

---

## 6. Repository Contribution & Pull Request Workflow

When contributing improvements or fixes to this repository:
1. **Never push directly to `main`**: Create a descriptive branch (`feat/<name>`, `fix/<name>`, `docs/<name>`).
2. **Run all local quality gates before opening a PR**:
   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src
   uv run pytest --cov=google_ai_history --cov-report=term-missing --cov-fail-under=85
   ```
3. **Open a Pull Request & Enable Auto-Merge (`--auto`)**:
   - Push the branch, open the PR, and enable GitHub Auto-Merge so GitHub automatically squash-merges and deletes the branch the moment all 6 required CI status checks (`ubuntu-latest` + `windows-latest` across Python `3.11`, `3.12`, `3.13`) pass:
     ```bash
     git push -u origin <branch-name>
     gh pr create --fill
     gh pr merge --auto --squash --delete-branch
     gh pr checks --watch --fail-fast
     ```

---

## 7. Automated Issue Discovery & Triage Loop

To auto-discover and fix `[Agent Report]` issues (e.g., via an Antigravity scheduled automation or manual triage pass):
1. Query open owner-authored or `agent-report` issues that do not already have a linked open PR:
   ```bash
   gh issue list --repo travispflanz/google-ai-history-mcp --author travispflanz --state open --json number,title,body,labels
   ```
2. For each actionable issue, inspect the code on a fresh `fix/issue-<number>` branch from `main`, add a regression test, verify locally (and against Chrome CDP `127.0.0.1:9222` when browser DOM selectors are affected), run all 4 CI gates above, and open a PR with `Closes #<number>` and `gh pr merge --auto --squash --delete-branch`.
