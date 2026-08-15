# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project summary

SJTU Agent is a deployable campus assistant for Shanghai Jiao Tong University. It provides an LLM-powered CLI chat interface, multi-platform bots (Telegram, Feishu/Lark, WeChat, QQ), DDL aggregation across four campus platforms, daily reports, reminder daemons, a news aggregator, a homework agent (Canvas + Claude Code), a web config UI, and an MCP server for AI agent integration.

## Setup & development

```bash
# Initial setup (creates .venv, installs in editable mode, optional Playwright)
bash install/install.sh          # macOS/Linux
powershell -ExecutionPolicy Bypass -File .\install\install.ps1  # Windows

# Manual install
python -m venv .venv && source .venv/bin/activate && pip install -e .

# Configuration
cp .env.example .env     # edit with real credentials
sjtu-agent setup         # interactive config wizard, writes config.json + agent_config.json

# Running
sjtu-agent               # interactive CLI chat (default)
sjtu-agent doctor        # print runtime paths and config status
sjtu-agent web           # open web config UI at localhost:7860
sjtu-agent telegram-bot  # start Telegram bot daemon
sjtu-agent feishu-bot    # start Feishu bot daemon
sjtu-agent wechat-bot    # start WeChat bot daemon
sjtu-agent qq-bot        # start QQ bot daemon
sjtu-agent ddl           # run DDL check once
sjtu-agent daily-report  # generate and send daily report
sjtu-agent remind-check  # run reminder daemon once
sjtu-agent news-digest   # run news digest
sjtu-agent mcp           # start MCP server for DDL queries
sjtu-agent login         # refresh platform cookies via Playwright
sjtu-agent install-daemons  # install background services
sjtu-agent update        # git pull + reinstall
```

## Testing

```bash
pytest                          # run all tests
pytest tests/test_config.py     # run a single test file
pytest -k "test_function_name"  # run tests matching a pattern
```

Tests live in `tests/`, discovered via `pytest.ini`. Configuration is minimal — coverage is not configured yet. A CI workflow runs the suite on GitHub (`.github/workflows/test.yml`, currently ~478 tests).

## Environment variables

| Variable | Purpose |
|---|---|
| `JACCOUNT_USERNAME` | jAccount login username for my.sjtu.edu.cn |
| `JACCOUNT_PASSWORD` | jAccount password |
| `ZHIYUAN_API_KEY` | SJTU Zhiyuan No.1 API key (OpenAI-compatible) |
| `ANTHROPIC_API_KEY` | Optional, used for captcha recognition via Claude Haiku |
| `SJTU_AGENT_HOME` | Override the runtime data directory (default: platform-specific user data dir) |
| `SJTU_HOMEWORK_DIR` | Override the homework/assignments directory |

## Architecture

### Package structure (`sjtu_agent/`)

```
sjtu_agent/
  cli.py              # argparse entry point — all subcommands dispatch from here
  paths.py            # centralized runtime paths (DATA_DIR, CONFIG_PATH, etc.) + atomic_write_json
  config.py           # ConfigStore singleton — typed, cached, hot-reloading access to config.json
  setup_wizard.py     # interactive first-run configuration (large, ~54KB)
  terminal_ui.py      # Rich-powered terminal helpers
  commands/           # shared slash-command metadata, prompt resolver, and execution handlers
  homework_agent.py   # Canvas homework fetching + Claude Code analysis
  vision.py           # dual-model vision — standalone vision model for image recognition
  logging.py          # unified logging (RotatingFileHandler + stdout mirror)
  notifications.py    # cross-platform notifications (Telegram/Feishu/Windows Toast)

  bots/               # shared bot session core, reused by all 4 bots
    _core.py          # build_date_ctx / make_session / run_one_turn(+multimodal) / extract_assistant_reply

  agent/              # LLM chat engine (refactored from root-level agent.py in 2026-05)
    __init__.py       # re-exports all public symbols
    prompts.py        # SYSTEM_PROMPT, date context builder, tool labels (~22KB)
    tools/            # TOOLS definitions + tool_xxx implementations (split by domain, _core.py kernel)
    runner.py         # LLM client creation, single-turn execution, streaming
    chat_loop.py      # config loading, chat main loop, startup logic

  scheduler/          # cross-platform daemon manager
    __init__.py       # facade — dispatches to platform backend by sys.platform
    launchd.py        # macOS launchd plist generator
    systemd.py        # Linux systemd unit generator
    taskschd.py       # Windows Task Scheduler integration
    psmuxd.py         # Windows psmux (detached session) integration

  news_aggregator/    # intelligent news collection + ranking system
    aggregator.py     # concurrent multi-source fetching
    profile.py        # user profile learned from chat history
    ranker.py         # LLM-based + keyword news ranking
    digest.py         # markdown + HTML digest builder
    storage.py        # news storage and deduplication
    sources/          # per-platform scrapers (jwc, shuiyuan, official, canvas)

  tui/                # Textual full-screen chat TUI (optional extra [tui])
    app.py            # ChatApp: session list, Markdown messages, slash completion, modals
    engine.py         # adapts the Web SSE chat/command streams and approvals
    session_model.py  # reuses web_sessions.sqlite3 so Web and TUI stay in sync
    cards.py          # structured command results rendered as terminal Markdown cards
    attachments.py    # whitelisted attachment staging copied into web_attachments/
    commands.py       # slash-command completion candidates
    messages.py       # command-result JSON and date-context message helpers

  web/                # local Web GUI + legacy config page
    server.py         # pure stdlib HTTP server: SSE chat (/api/chat), command SSE (/api/command), config APIs
    session_store.py  # SQLite-backed Web sessions/messages
    attachment_store.py  # uploaded attachment metadata/files
    attachment_context.py  # shared attachment pre-parsing and context injection
    static/           # built React bundle (source in webui/src/, rebuild with npm run build:webui)
```

### Key design patterns

**ConfigStore singleton** (`config.py`): All config access goes through `ConfigStore.get_instance()`, not raw `json.loads()`. It caches values, tracks file mtime for hot reload, and provides typed accessors. Test it by mocking the singleton.

**Atomic writes** (`paths.py`): Always use `atomic_write_json(path, data)` for persistent state files — it writes to a temp file and `os.replace()`s, so crashes never leave half-written files that could trigger "re-send all reminders" bugs.

**Runtime layout**: Data lives in a platform-specific user data directory (`~/.local/share/sjtu-agent` on Linux, `~/Library/Application Support/sjtu-agent` on macOS, `%APPDATA%/sjtu-agent` on Windows). On first run, `ensure_runtime_layout()` migrates legacy files (`.env`, `config.json`, etc.) from the project root.

**Agent tool system** (`agent/tools/`): Each tool is a `tool_xxx` function + a `TOOLS` dict entry. The `run_tool(name, args)` function dispatches by name. Tools are split by domain across submodules (`_reminders.py`, `_email.py`, `_mcp_skills.py`, `_platforms.py`, etc.), with the tightly-coupled kernel remaining in `_core.py` (~3700 lines).

**Bot architecture**: All four bots (Telegram, Feishu, WeChat, QQ) share the same `agent/chat_loop.py` engine. Each bot adds its own platform-specific message handling layer. A shared session core lives in `sjtu_agent/bots/_core.py`; a full `BaseBotRunner` abstraction (transport/media/commands) is still planned but deferred.

### Root-level layout

Three Python modules stay at root because they are imported as bare top-level modules from within `sjtu_agent/`:
- `agent.py` — re-exports from `sjtu_agent.agent`, backwards-compat entry point
- `ddl_checker.py` — large standalone module (~90KB), not yet refactored into the package
- `login.py` — Playwright login automation, imported by `ddl_checker.py` and `agent/tools/_core.py`

Entry-point scripts live in `scripts/`:
- `telegram_bot.py`, `feishu_bot.py`, `wechat_bot.py` — bot daemons
- `daily_report.py`, `remind_check.py`, `care_check.py` — scheduled daemons
- `mcp_server.py`, `news_digest.py`, `shuiyuan_watcher.py` — servers/watchers
- `setup_config.py` — cookie import utility

Install and launcher shell scripts are in `install/`. Design docs live in `docs/`.

New code should go into `sjtu_agent/`, not these root or script files.

### Refactoring status

Refactoring status (as of v0.19.0):
- **Done**: agent.py split into `sjtu_agent/agent/`; tools split into `agent/tools/` subpackage; ConfigStore + protected write path; `run_tool` registry; Notifier abstraction; unified logging; Web GUI (multi-session, attachments, approvals); shared slash-command layer (`sjtu_agent/commands/`); Textual TUI (`sjtu-agent tui`, reuses the Web SQLite session store and SSE engine); TUI command cards, session rename/delete, and whitelisted `/attach` flow
- **Partial/deferred**: full BaseBotRunner transport abstraction to deduplicate all bot glue; BasePlatform for DDL scrapers; `/hw do` fine-grained progress callbacks
- CI workflow exists (`.github/workflows/test.yml`, installs `[tui]`) but coverage is thin
