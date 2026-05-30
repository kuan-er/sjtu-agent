import asyncio
import json


def test_build_skill_prompt_loads_enabled_builtin_skill(tmp_path, monkeypatch):
    skill_root = tmp_path / "extra-skills"
    skill_dir = skill_root / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Use this skill for demo workflows.", encoding="utf-8")

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"skills": {"enabled": ["demo"], "dirs": [str(skill_root)]}}),
        encoding="utf-8",
    )

    from sjtu_agent.extensions import skills

    monkeypatch.setattr(skills, "CONFIG_PATH", config_path)

    prompt = skills.build_skill_prompt()
    assert "Skill: demo" in prompt
    assert "demo workflows" in prompt


def test_get_available_tools_with_no_mcp_config_does_not_raise(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"mcp_servers": {}}), encoding="utf-8")

    from sjtu_agent.extensions import mcp_client
    from sjtu_agent.extensions.registry import get_available_tools

    monkeypatch.setattr(mcp_client, "CONFIG_PATH", config_path)
    mcp_client._TOOLS_CACHE.update({"ts": 0.0, "tools": [], "map": {}})

    tools = get_available_tools(force_refresh=True)
    names = [tool["function"]["name"] for tool in tools]
    assert "get_ddls" in names


def test_mcp_async_runner_works_inside_existing_event_loop():
    from sjtu_agent.extensions import mcp_client

    async def sample():
        return "ok"

    async def main():
        return mcp_client._run_async(lambda: sample())

    assert asyncio.run(main()) == "ok"


def test_builtin_run_tool_routes_mcp_prefix(monkeypatch):
    from sjtu_agent.agent import tools
    from sjtu_agent.extensions import mcp_client

    monkeypatch.setattr(mcp_client, "call_tool", lambda name, args: json.dumps({"name": name, "args": args}))

    result = json.loads(tools.run_tool("mcp__demo__echo", {"text": "hi"}))
    assert result == {"name": "mcp__demo__echo", "args": {"text": "hi"}}


def test_add_mcp_server_requires_confirmation():
    from sjtu_agent.agent.tools import tool_add_mcp_server

    result = tool_add_mcp_server(
        server_id="demo",
        transport="stdio",
        command="python",
        args=["server.py"],
    )
    assert result["requires_confirmation"] is True
    assert result["external_target"] == "python server.py"
    assert "acknowledge_external_mcp=true" in result["next_action"]


def test_add_mcp_server_writes_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    from sjtu_agent.agent import tools
    from sjtu_agent.extensions import mcp_client

    monkeypatch.setattr(tools, "CONFIG_PATH", config_path)
    monkeypatch.setattr(mcp_client, "list_openai_tools", lambda force_refresh=False: [])

    result = tools.tool_add_mcp_server(
        server_id="demo",
        transport="sse",
        url="http://127.0.0.1:8765/sse",
        acknowledge_external_mcp=True,
    )
    assert result["ok"] is True

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["demo"]["url"] == "http://127.0.0.1:8765/sse"
    assert cfg["mcp_servers"]["demo"]["enabled"] is True


def test_add_skill_writes_and_enables(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    from sjtu_agent.agent import tools

    monkeypatch.setattr(tools, "CONFIG_PATH", config_path)

    result = tools.tool_add_skill("demo-skill", content="Use this skill for demos.")
    assert result["ok"] is True

    skill_file = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    assert skill_file.read_text(encoding="utf-8").strip() == "Use this skill for demos."

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["skills"]["enabled"] == ["demo-skill"]


def test_create_skill_requires_clarification_for_missing_details(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    from sjtu_agent.agent import tools

    monkeypatch.setattr(tools, "CONFIG_PATH", config_path)

    result = tools.tool_create_skill()
    assert result["requires_more_info"] is True
    assert result["questions"]
    assert not (tmp_path / "skills").exists()


def test_create_skill_generates_and_enables_skill(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    from sjtu_agent.agent import tools

    monkeypatch.setattr(tools, "CONFIG_PATH", config_path)

    result = tools.tool_create_skill(
        name="planner",
        purpose="the user asks to plan a study week.",
        triggers=["plan my week"],
        instructions="Ask for deadlines, then create a day-by-day plan.",
    )
    assert result["ok"] is True

    skill_file = tmp_path / "skills" / "planner" / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    assert "Use this skill when the user asks to plan a study week." in text
    assert "Ask for deadlines" in text

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["skills"]["enabled"] == ["planner"]


def test_list_skills_includes_user_skill(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"skills": {"enabled": ["planner"], "dirs": []}}), encoding="utf-8")
    skill_dir = tmp_path / "skills" / "planner"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Planner\nUse for planning.", encoding="utf-8")

    from sjtu_agent.agent import tools

    monkeypatch.setattr(tools, "CONFIG_PATH", config_path)

    result = tools.tool_list_skills()
    user_skills = [item for item in result["skills"] if item["source"] == "user"]
    assert user_skills == [
        {
            "name": "planner",
            "enabled": True,
            "source": "user",
            "path": str(skill_dir / "SKILL.md"),
            "summary": "Planner",
        }
    ]


def test_manage_skill_enable_disable_delete_user_skill(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"skills": {"enabled": ["planner"], "dirs": []}}), encoding="utf-8")
    skill_dir = tmp_path / "skills" / "planner"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Planner\nUse for planning.", encoding="utf-8")

    from sjtu_agent.agent import tools

    monkeypatch.setattr(tools, "CONFIG_PATH", config_path)

    disabled = tools.tool_manage_skill("disable", "planner")
    assert disabled["ok"] is True
    assert disabled["enabled"] is False

    enabled = tools.tool_manage_skill("enable", "planner")
    assert enabled["ok"] is True
    assert enabled["enabled"] is True

    deleted = tools.tool_manage_skill("delete", "planner")
    assert deleted["ok"] is True
    assert deleted["deleted"] is True
    assert not skill_dir.exists()

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["skills"]["enabled"] == []
