# AGENT.md

This file is for coding agents and maintainers working on `sjtu-agent`.
It summarizes the project shape, extension points, and the conventions that
matter most when changing the repo.

## Project Shape

`sjtu-agent` is a Python campus assistant for SJTU students. It has several
entrypoints that should keep the same behavior:

- Terminal chat: `agent.py`, `sjtu_agent/agent/chat_loop.py`
- Web UI: `sjtu_agent/web/server.py`
- Telegram bot: `telegram_bot.py`
- Feishu bot: `feishu_bot.py`
- WeChat bot: `wechat_bot.py`
- External MCP server: `mcp_server.py`
- CLI wrapper: `sjtu_agent/cli.py`

The root `agent.py` is a compatibility shim. Most new agent logic should live
under `sjtu_agent/agent/` or `sjtu_agent/extensions/`.

## Runtime Paths

Do not assume runtime state lives in the repo root. Use `sjtu_agent.paths`.

Important paths:

- `CONFIG_PATH`: campus credentials and feature config
- `AGENT_CONFIG_PATH`: LLM provider config
- `ENV_PATH`: local environment file
- `DATA_DIR`: user data directory, overrideable with `SJTU_AGENT_HOME`

Tests and setup flows often patch these paths, so avoid caching path-derived
state too early unless there is an explicit invalidation path.

## Tool Architecture

The historical built-in tools are defined in `sjtu_agent/agent/tools.py` as
OpenAI function-calling schemas plus `tool_*` implementations.

New code should use the dynamic registry:

- `sjtu_agent.extensions.registry.get_available_tools()`
- `sjtu_agent.extensions.registry.run_registered_tool(name, args)`

The registry combines:

- built-in tools from `sjtu_agent/agent/tools.py`
- enabled external MCP tools

Keep `agent.TOOLS` and `agent.run_tool` backward compatible because existing
bots and older scripts may import them. New integrations should prefer the
registry methods or the re-exported `agent.get_available_tools()` and
`agent.run_registered_tool()`.

## MCP Client Support

External MCP client support lives in `sjtu_agent/extensions/mcp_client.py`.

Supported transports:

- `stdio`
- `sse`
- `streamable_http` / `http`

Config shape in `config.json`:

```json
{
  "mcp_servers": {
    "server_id": {
      "enabled": true,
      "transport": "stdio",
      "command": "python",
      "args": ["server.py"],
      "cwd": "optional/working/dir",
      "env": {},
      "call_timeout": 120
    }
  }
}
```

MCP tools are exposed to the LLM as:

```text
mcp__<server_id>__<tool_name>
```

Custom MCP servers can be registered without code changes:

- CLI: `python -m sjtu_agent.cli add-mcp-server my-tools --transport stdio --command python --arg /path/to/server.py`
- Chat: route "add/register/connect a custom MCP server" requests to
  `add_mcp_server`. The first chat call must leave
  `acknowledge_external_mcp=false`; only call again with true after the user
  confirms the external command or URL.

The MCP adapter intentionally opens short-lived sessions for discovery and
tool calls. This keeps the synchronous runner simple and avoids long-lived
subprocess lifecycle bugs. If persistent sessions are added later, keep the
current behavior as a reliable fallback.

## Skill Support

Prompt-only skill support lives in `sjtu_agent/extensions/skills.py`.

Config shape in `config.json`:

```json
{
  "skills": {
    "enabled": [],
    "dirs": []
  }
}
```

Skill files are loaded from:

- bundled skills: `sjtu_agent/skills/<name>/SKILL.md`
- repo-local skills: `skills/<name>/SKILL.md`
- user data skills: `<DATA_DIR>/skills/<name>/SKILL.md`
- extra directories listed in `skills.dirs`

`"enabled": ["*"]` is supported and loads every `SKILL.md` found in the
configured skill directories. Prefer explicit skill names for normal user
configs so prompts remain predictable.

The active system prompt should be built with:

```python
from sjtu_agent.agent.prompts import build_system_prompt
```

Do not append directly to `SYSTEM_PROMPT` in entrypoints. Use
`build_system_prompt(...)` so enabled skills are included consistently.

Custom skills can be added without code changes:

- CLI: `python -m sjtu_agent.cli add-skill my-skill --content-file /path/to/SKILL.md`
- CLI: `python -m sjtu_agent.cli list-skills`
- CLI: `python -m sjtu_agent.cli manage-skill disable my-skill`
- Chat: route "add a custom skill" requests to `add_skill` after the user
  provides either full `SKILL.md` content or a local source file path.
- Chat: route "create a skill" / "skill creator" requests to `create_skill`.
  If the tool returns `requires_more_info`, ask the returned questions before
  trying again. If the user already provides a clear purpose and instructions,
  create and enable the skill directly.
- Chat: route "list skills" requests to `list_skills`, and route skill
  enable/disable/delete requests to `manage_skill`.

## Prompt and Entry Points

Multiple entrypoints run their own tool loops. When changing tool plumbing,
check all of these:

- `sjtu_agent/agent/runner.py`
- `sjtu_agent/web/server.py`
- `telegram_bot.py`
- `feishu_bot.py`
- `wechat_bot.py`

They should all use dynamic tools and `build_system_prompt`.

## Safety Rules

High-impact actions need confirmation or a draft-first flow:

- sending email
- submitting Canvas assignments
- posting or replying on Shuiyuan
- making irreversible account/config changes

When adding write tools, make the tool result explicit: target, action,
success state, and any resulting URL or id.

## Development Notes

- Prefer focused changes over broad refactors.
- Keep user runtime files out of git.
- Do not overwrite unrelated dirty work.
- Use `rg` for code search.
- Use `python -m compileall sjtu_agent ...` for a quick syntax check.
- The repo tests use `pytest`; if it is not installed in the current venv,
  report that instead of pretending tests passed.

Useful checks:

```bash
python -m compileall sjtu_agent agent.py telegram_bot.py feishu_bot.py wechat_bot.py mcp_server.py
python -m sjtu_agent.cli --help
python -m pytest
```
