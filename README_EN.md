# SJTU Agent

[![Test](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml)

A campus assistant for Shanghai Jiao Tong University students, offering terminal chat, Telegram / Feishu (Lark) / WeChat / QQ bots, DDL aggregation, daily reports, campus news, canteen recommendations, and an MCP server.

中文文档: [README.md](README.md)

👉 **[Project Showcase](https://kuan-er.github.io/sjtu-agent)**

If this project helps you, please consider giving it a ⭐ Star!

## Installation

macOS / Linux:

```bash
git clone https://github.com/kuan-er/sjtu-agent.git && cd sjtu-agent && bash install.sh
```

Windows PowerShell:

```powershell
git clone https://github.com/kuan-er/sjtu-agent.git; cd sjtu-agent; powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The install script automatically creates `.venv`, installs dependencies, installs Playwright Chromium, then launches `sjtu-agent setup`.

The setup wizard first saves your LLM API config, then guides you through saving campus platform credentials, auto-creating a Canvas Token, importing cookies from Chrome, and optionally installing launchd services on macOS. During setup you can use natural language replies or shortcut commands: `status`, `help`, `skip`, `quit`, `open canvas`, `auto canvas`.

## Advanced Install Options

Skip setup or Chromium:

```bash
# macOS / Linux
bash install.sh --no-setup
bash install.sh --skip-playwright
```

```powershell
# Windows
.\install.ps1 -NoSetup
.\install.ps1 -SkipPlaywright
```

Manual install:

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e .
sjtu-agent setup
```

## Zhiyuan No.1 API (Recommended)

[Zhiyuan No.1](https://zhiyuan.sjtu.edu.cn) is SJTU's official LLM platform with OpenAI-compatible APIs. Free for SJTU students and staff.

Run `sjtu-agent setup` after installation — the wizard prompts for your API key and saves it automatically.

For manual setup, add this to `.env` in your runtime data directory:

```bash
ZHIYUAN_API_KEY=your-api-key
```

Default Base URL: `https://models.sjtu.edu.cn/api/v1`. Default model: `deepseek-chat` (DeepSeek V3.2 hosted at SJTU).

Available models:

| Model ID | Description |
|----------|-------------|
| `deepseek-chat` | DeepSeek V3.2 (**default**) |
| `deepseek-reasoner` | DeepSeek V3.2 (reasoning mode) |
| `glm-5` | GLM 5.0 |
| `minimax` / `minimax-m2.5` | MiniMax M2.5 |
| `qwen3coder` | Qwen3-Coder-30B |
| `qwen3vl` | Qwen3-VL-32B |

**How to apply:** Go to [zhiyuan.sjtu.edu.cn](https://zhiyuan.sjtu.edu.cn), log in with jAccount, create a key under "API Management".

For DeepSeek official or other OpenAI-compatible backends, select "Custom" in the web config page and fill in your API key, Base URL, and model name.

## Vision Model (Optional, for Image Recognition)

If your main model does not support vision input (e.g. `deepseek-chat`), you can configure a separate vision model (e.g. `qwen-vl-max`). When Feishu receives an image, the vision model recognizes it first, with OCR as a fallback. **Choose any one of three configuration methods:**

1. **Interactive**: run `sjtu-agent setup`. After configuring the main model, follow the prompts to configure the vision model (API key input is **not echoed**).

2. **Command line**:
   ```bash
   sjtu-agent setup \
     --vision-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
     --vision-api-key your-vision-api-key \
     --vision-model qwen-vl-max \
     --vision-enabled
   ```

3. **Manual edit** `agent_config.json`:
   ```json
   "vision_model": {
     "enabled": true,
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "api_key": "your-vision-api-key",
     "model": "qwen-vl-max"
   }
   ```

> The vision model is used only for image recognition (one-shot calls) and does not participate in conversation history. The API key is stored locally and is neither echoed nor printed. See [agent_config.example.json](agent_config.example.json) for a full template.

---

## Common Commands

```bash
sjtu-agent                # start interactive chat
sjtu-agent setup          # first-run config wizard
sjtu-agent doctor         # show config status and runtime paths
sjtu-agent setup-config   # build config.json from browser cookies
sjtu-agent login --aihaoke
sjtu-agent ddl --canvas-only
sjtu-agent daily-report --test
sjtu-agent telegram-bot --test
sjtu-agent qq-bot --test
sjtu-agent email-watcher --once
sjtu-agent canvas-watcher --once
sjtu-agent remind-check --list
sjtu-agent mcp --http --port 8765
sjtu-agent add-mcp-server my-tools --transport stdio --command python --arg D:/path/to/server.py
sjtu-agent add-skill my-skill --content-file D:/path/to/SKILL.md
sjtu-agent install-daemons
```

Or run as a module:

```bash
python -m sjtu_agent
```

Setup variants:

```bash
sjtu-agent setup
sjtu-agent setup --yes --skip-cookie-import --skip-launchd
sjtu-agent setup --yes --write-daemons-only --output-dir /tmp/sjtu-agent-launchd
```

## MCP & Skills

The Agent can expose its own tools as an MCP server and load external MCP servers as extra tools. External MCP server config lives in `config.json` under `mcp_servers`; enabled prompt-only skills live under `skills.enabled`.

Add a custom MCP server:

```bash
sjtu-agent add-mcp-server my-tools --transport stdio --command python --arg D:/path/to/server.py
sjtu-agent add-mcp-server remote-tools --transport sse --url http://127.0.0.1:8765/sse
```

You can also ask the agent in chat to "add a custom MCP server". The first call warns that an external command or URL will be trusted and requires explicit confirmation.

Add a custom prompt-only skill:

```bash
sjtu-agent add-skill my-skill --content-file D:/path/to/SKILL.md
sjtu-agent list-skills
sjtu-agent manage-skill disable my-skill
```

For a more agent-native flow, ask the agent to "create a skill" and describe the behavior you want. If the requirement is unclear, the agent asks follow-up questions. You can also list, enable, disable, or delete skills through `list_skills` and `manage_skill`.

## macOS Services

Install built-in launchd services with one command:

```bash
sjtu-agent install-daemons
```

This writes LaunchAgent plists to `~/Library/LaunchAgents` and loads them into your user session. The Web UI (`http://127.0.0.1:7860`) opens automatically after installation.

- `web`: Web config UI, starts at login, kept alive by launchd
- `daily-report`: runs daily at `22:00`
- `remind-check`: runs every `60` seconds
- `canvas-watcher`: monitors Canvas course announcements and quizzes
- `telegram-bot`: starts at login, kept alive by launchd
- `qq-bot`: starts at login, kept alive by launchd

Common variants:

```bash
sjtu-agent install-daemons --write-only
sjtu-agent install-daemons --services daily-report remind-check canvas-watcher
sjtu-agent install-daemons --daily-report-time 21:30 --remind-interval 120
```

Services use the current Python interpreter, the runtime data directory as working directory, and write logs to `~/Library/Application Support/sjtu-agent/logs`.

## Windows Services

Windows offers two backend options: **Task Scheduler** (default, for scheduled tasks) and **psmux** (for persistent processes).

### Default (Task Scheduler)

```powershell
sjtu-agent install-daemons
```

Daily report at 22:00 + reminder check every 60s + Canvas course monitoring + Telegram/Feishu/WeChat/QQ Bots at logon + Web UI.

### psmux for Persistent Processes

[psmux](https://github.com/psmux/psmux) is a Windows tmux alternative for running bots in detached sessions with no terminal windows.

```powershell
# Install psmux
winget install psmux

# Start persistent bots
sjtu-agent install-daemons --backend psmux --services feishu-bot telegram-bot

# Scheduled tasks still use Task Scheduler
sjtu-agent install-daemons --backend taskschd --services daily-report remind-check

# Check psmux status
psmux -L sjtu-agent ls
```

**Comparison:**

| | Task Scheduler (`taskschd`) | psmux |
|---|---|---|
| Best for | Scheduled tasks, startup triggers | Persistent processes |
| Popups | None (`pythonw.exe`) | None |
| Crash restart | Manual | Manual (re-pull session) |
| Auto-start at boot | ✅ | ❌ |
| Status check | `schtasks /Query` | `psmux -L sjtu-agent ls` |
| Stop | `schtasks /Delete` | `psmux kill-session -t <name>` |

> **Recommended**: `daily-report` and `remind-check` via taskschd (scheduled); `feishu-bot`, `telegram-bot`, `wechat-bot`, `qq-bot`, `web` via psmux (persistent).

### Desktop Launcher (Windows)

A GUI launcher for managing bot services without the command line:

```batch
install\launch-feishu.bat
```

Features: start/stop bots, view running status, no terminal windows. Requires psmux installed.

## Using the Feishu Bot

> Each user needs to create their own Feishu app as the bot's "shell" — Feishu does not support public multi-tenant bots. This takes about 5 minutes.

### Step 1: Create an App on Feishu Open Platform

1. Go to https://open.feishu.cn and log in (scan with mobile Feishu)
2. Go to **Developer Console** → **Create Enterprise App**
   - App name: anything (e.g., "SJTU Agent")
   - Description and icon are optional
3. Note the **App ID** (`cli_xxxxxxxxxxxx`) and **App Secret**

### Step 2: Enable Bot Capabilities & Permissions

In your new app:

1. **Add Application Capability** → find **Bot** → enable
2. **Permission Management** → search and request these 3 permissions (no approval needed, effective immediately):
   - `im:message`
   - `im:message.p2p_msg:readonly`
   - `im:message:send_as_bot`

### Step 3: Subscribe to Message Events (WebSocket)

1. **Events & Callbacks** → **Event Configuration**
2. Switch to **Long Connection** mode (NOT callback URL)
3. **Add Event** → search `im.message.receive_v1` → add

### Step 4: Publish the App (critical!)

> If not published, the bot won't appear in Feishu search.

1. **Version Management & Publishing** → **Create Version**
   - Version: `1.0.0`
   - Scope: "All members" or "Specific members (including yourself)"
2. Click **Apply for Publishing**
3. Approval: if you use a personal Feishu account, you are the admin — open mobile Feishu → Workspace → Admin Console → App Review → approve

### Step 5: Configure in SJTU-Agent

1. Run `sjtu-agent web` to open the WebUI
2. Find the **Feishu Bot (Lark)** card and fill in:
   - **App ID**: the `cli_xxx` from Step 1
   - **App Secret**
   - **Allowed user open_id**: leave empty for now
3. Click **Save**
4. Click **Start Feishu Bot** (on macOS it registers as a launchd service)

### Step 6: Find the Bot and Restrict Access

1. Open Feishu and search for your app name → tap it → send a message
2. The terminal prints your `open_id`:
   ```
   [feishu] ℹ whitelist is empty — add this open_id: ou_xxxxxxxxxxxx
   ```
3. Copy `ou_xxx`, go back to WebUI → Feishu card → paste → Save → restart the bot

### FAQ

| Issue | Solution |
|---|---|
| WebUI start fails | macOS supports one-click start; Windows uses `sjtu-agent install-daemons --backend psmux --services feishu-bot` |
| Can't find bot in Feishu | Version not published / review not approved / scope doesn't include you |
| Bot online but won't reply | Events & Callbacks not switched to Long Connection, or `im.message.receive_v1` not subscribed |
| Permission denied error | Forgot to request `im:message:send_as_bot` |
| Verify App ID/Secret | Run `sjtu-agent feishu-bot -- --test` (note the `--`) |
| Find your open_id | Run `sjtu-agent feishu-bot -- --whoami`, then send any message |

## Feishu Bot Slash Commands

**Conversation Management**

| Command | Description |
|---|---|
| `/new <name>` | Create and switch to a new conversation |
| `/list` | List all conversations |
| `/switch <number>` | Switch to a conversation |
| `/name <number> <new-name>` | Rename a conversation |
| `/delete <number>` | Delete a conversation |
| `/history` | Show recent messages |
| `/help` | Show command help |

**Homework Assistant** (requires Claude Code CLI)

| Command | Description |
|---|---|
| `/hw` | List pending Canvas assignments |
| `/hw past` | List past (submitted) assignments |
| `/hw do <number>` | Analyze assignment (solution approach only) |
| `/hw past do <number>` | Analyze a past assignment |
| `/hw brief <number>` | Show assignment summary only |
| `/hw due <N>` | List assignments due within N days |
| `/hw answer` | Get full solution (after running `/hw do`) |

Reply "给我答案" (give me the answer) after `/hw do` for the complete solution with AI disclaimers.

**LaTeX Templates**

| Command | Description |
|---|---|
| `/template` | List available templates |
| `/template bachelor-thesis` | Apply SJTU thesis template |

**Campus News & Dining**

| Command | Description |
|---|---|
| `/news` | Get the campus news digest |
| `/news_block <category>` | Block a news category |
| `/news_reset` | Reset the news profile |
| `/eat` | Canteen recommendation (Minghang campus) |
| `/eat 徐汇` | Canteen recommendation (Xuhui campus) |
| `/eat 张江` | Canteen recommendation (Zhangjiang campus) |

## Using the QQ Bot

1. Register at [q.qq.com/qqbot/openclaw](https://q.qq.com/qqbot/openclaw/), get `AppID` / `AppSecret`.
2. Ask the agent to call `setup_qq`, or manually write `config.json`.
3. Start the bot: `sjtu-agent qq-bot` (test first: `sjtu-agent qq-bot --test`).
4. Have the authorized QQ account send a message to get its QQ user ID (not QQ number).
5. Manage the whitelist:
   - `qq_add_user`: add a user
   - `qq_list_users`: list whitelist
   - `qq_remove_user`: remove a user
6. Restart `sjtu-agent qq-bot` after whitelist changes. For persistence: `sjtu-agent install-daemons --services qq-bot`.

### 📧 SJTU Email Notifications

Automatically checks mail.sjtu.edu.cn and pushes new email notifications to Feishu. Read-only messenger — never sends, deletes, or modifies any email.

```bash
sjtu-agent email-watcher          # run continuously (checks every 60s)
sjtu-agent email-watcher --once   # check once
sjtu-agent install-daemons --services email-watcher
```

Notifications include sender, subject, timestamp, and a 200-character body preview. Requires jAccount credentials.

### MATLAB Figures (Optional)

If MATLAB (R2020a+) is installed locally, Claude Code can generate high-quality vector illustrations and embed them in PDF solutions:

1. Claude Code writes `_figures.m` (`figure(); plot(...); exportgraphics(gcf,'fig1.pdf','ContentType','vector')`)
2. Runs `matlab -batch` → vector PDF output
3. Embeds via `\includegraphics{fig1.pdf}` in `_解答.tex`

Use `MATLAB_PATH` env var to override auto-detection. Falls back to Matplotlib if MATLAB is unavailable.

> MiKTeX (`winget install MiKTeX.MiKTeX`) is required for xelatex compilation. Install the ctex Chinese macro package: `mpm --install ctex`.

### 📄 SJTU LaTeX Templates

Built-in SJTU bachelor thesis template. Apply standard formatting and compile to PDF.

```bash
# In Feishu Bot
/template                           # list available templates
/template bachelor-thesis           # apply thesis template to PAPERS_DIR

# Set papers directory (.env)
SJTU_PAPERS_DIR=E:/sjtu/sjtu-agent-papers-area
```

Place your documents in `SJTU_PAPERS_DIR`, then say "帮我格式化" (format for me). The agent auto-fills content into the template and compiles with xelatex. Template source: [sjtug/SJTUThesis](https://github.com/sjtug/SJTUThesis).

## Multimodal Parsing (Optional)

Supports OCR for text-in-image content, scanned PDF parsing, and ASR audio transcription. Requires optional backend installation:

```bash
sjtu-agent install-parse-backends --backend pdf_ocr    # PDF/image OCR
sjtu-agent install-parse-backends --backend whisper    # voice transcription
sjtu-agent install-parse-backends --backend all        # install all
```

Leverages PaddleOCR and openai-whisper. Fully integrated across all bot platforms.

## AI News

The Feishu Bot includes `/aihot`, a command that fetches curated daily AI news from the [aihot.virxact.com](https://aihot.virxact.com) public API (MIT, no key required).

```bash
# Feishu Bot
/aihot                              # fetch today's curated AI news

# Terminal / cron
sjtu-agent aihot                    # fetch and push to Feishu
python scripts/aihot_push.py --test # preview only
```

Inspired by [KKKKhazix/khazix-skills](https://github.com/KKKKhazix/khazix-skills) `ai-hot` (MIT).

## Campus News

Intelligent news aggregation — collects from four sources (jwc 教务处, 水源 community, SJTU News Network, Canvas) and ranks in two stages (keyword pre-filter + LLM ranking), personalizing recommendations from a learned user profile. The Feishu Bot `/news` command fetches on demand; a scheduled push sends a digest every day at 10:00.

```text
/news                         # campus news digest
/news_block <category>        # block a news category
/news_reset                   # reset the news profile
```

```bash
sjtu-agent news-digest --dry-run   # preview
sjtu-agent news-digest --no-llm    # keyword-only ranking
sjtu-agent install-daemons         # register the daily news-digest task
```

## Canteen Recommendation

Combines the real-time crowding API at campuslife.sjtu.edu.cn with learned dining preferences to recommend the best place to eat. Supports fuzzy name matching (e.g. 「三餐」「哈乐」).

```text
/eat                    # Minghang campus recommendation
/eat 徐汇               # Xuhui campus recommendation
/eat 张江               # Zhangjiang campus recommendation
```

After eating, tell the bot "我去 XX 吃了" and it records your preference — future recommendations get more accurate.

## Daily Reports

Automatically generates a morning briefing (today's schedule + DDLs), a noon briefing (afternoon classes), and an evening report (tomorrow's schedule + AI study suggestions), pushed via Feishu / Telegram. Report modules can be toggled in natural conversation (e.g. "晚上别推送作业提醒").

```bash
sjtu-agent daily-report --test            # preview
sjtu-agent daily-report --type morning    # morning report
sjtu-agent install-daemons                # install scheduled push
```

## Memory

The Feishu Bot uses ChromaDB for cross-conversation semantic memory. After each conversation it extracts key facts (courses, exams, study preferences) and, in later conversations, retrieves relevant memories to inject into context. No configuration needed — auto-initialized on first use.

## Configuration

Three key runtime files:

- `config.json`: platform tokens, cookies, Telegram config
- `.env`: jAccount/MOOC credentials and Zhiyuan API key
- `agent_config.json`: LLM provider, Base URL, model name (unnecessary if `ZHIYUAN_API_KEY` is set in `.env`)

### Security Note

Credentials (API keys, passwords, tokens) are stored in plaintext in local files. The Web UI requires a `?token=xxx` access token (printed to the terminal on first launch). The `execute_python` tool strips sensitive environment variables while running. Keep the runtime data directory private (macOS/Linux set it to `0o600` automatically).

For Canvas, `sjtu-agent setup` tries to create and save an API token automatically when Playwright and jAccount credentials are available. If that fails, it opens `https://oc.sjtu.edu.cn/profile/settings` and falls back to manual confirmation.

### Canvas Course Queries and Monitoring

Canvas support is read-only. The agent can resolve a course by name, course code, or Canvas `course_id`, then fetch announcements, quizzes, course updates, and global todo/planner items. The watcher checks for new announcements and quiz changes and can notify through system notifications, Telegram, or Feishu.

Example requests:

- "List my Canvas courses"
- "Show recent announcements for ECE2300"
- "Check quizzes and due times for this course"
- "Show historical quizzes for this course" (explicitly includes expired items)
- "Summarize recent updates for this course"

Watcher commands:

```bash
sjtu-agent canvas-watcher --once --test   # check once and preview notifications
sjtu-agent canvas-watcher --once          # check once and send real notifications
sjtu-agent canvas-watcher                 # keep running, default interval 300s
sjtu-agent install-daemons --services canvas-watcher
```

Configure `canvas_monitor` in `config.json`:

```json
{
  "canvas_monitor": {
    "enabled": true,
    "course_ids": [92355],
    "course_filters": ["ECE2300"],
    "include_announcements": true,
    "include_quizzes": true,
    "include_assignments": false,
    "interval_seconds": 300,
    "notify_channels": ["system", "telegram", "feishu"],
    "baseline_on_first_run": true
  }
}
```

You can also ask the agent to update this configuration, for example:

- "Check Canvas every 10 minutes"
- "Only monitor ECE2300 and ECE2700"
- "Pause Canvas monitoring"
- "Enable assignment monitoring and notify through Feishu plus system notifications"

The agent calls `configure_canvas_monitor` and updates only the `canvas_monitor` block. The minimum interval is 30 seconds. A running watcher reloads the config on its next loop; restart it if you need the change immediately.

`course_ids` takes precedence over `course_filters`; if both are empty, all active courses are monitored. Quizzes and assignments return only still-current items by default, automatically excluding expired ones; ask for historical/expired items explicitly when needed. The first run creates a baseline by default, so old announcements and quizzes are not pushed all at once. Watcher state is saved as `canvas_monitor_state.json` in the runtime data directory, and logs go to `logs/canvas_watcher.log`. Quiz discovery uses Classic Quizzes first and supplements quiz-backed assignments; SJTU Canvas currently returns 404 for the separate New Quizzes API, so that endpoint is not a primary dependency.

## Runtime Data

Runtime files are stored in platform-specific user data directories, not the repo root:

- macOS: `~/Library/Application Support/sjtu-agent`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/sjtu-agent`
- Windows: `%APPDATA%/sjtu-agent`

Legacy files in the repo root are auto-migrated on first import.

## Robustness

The Feishu Bot self-checks credentials, ChromaDB, and Agent API connectivity on startup; writes a heartbeat file every 30s for the launcher to monitor (no heartbeat for >90s → unresponsive); and cleans up thread pools and temp files on exit.

## Version

Current version: **v0.8.0**. Release history: [Releases](https://github.com/kuan-er/sjtu-agent/releases).

## Release Notes

This repository is packaged for installable distribution with stable entry points. Core platform adaptation logic remains in top-level modules for behavioral stability.
