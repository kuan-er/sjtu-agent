from __future__ import annotations

import pytest

from sjtu_agent.commands import COMMANDS, command_defs, command_prompt


def test_command_defs_cover_webui_commands():
    data = command_defs()
    names = {item["name"] for item in data}
    assert {
        "/hw",
        "/news",
        "/news_block",
        "/news_reset",
        "/eat",
        "/template",
        "/ddl",
        "/help",
    } <= names

    for item in data:
        assert set(item) == {
            "name",
            "label",
            "icon",
            "description",
            "prompt",
            "examples",
            "chip",
        }
        assert item["name"].startswith("/")
        assert item["prompt"]
        assert isinstance(item["examples"], list)


def test_command_defs_ddl_examples_are_single_strings():
    """回归测试：("/ddl") 会被 Python 拆成字符，必须以 tuple 写法表达单元素。"""
    ddl = next(item for item in command_defs() if item["name"] == "/ddl")
    assert ddl["examples"] == ["/ddl"]


def test_chip_flags():
    data = {item["name"]: item["chip"] for item in command_defs()}
    assert {name for name, chip in data.items() if chip} == {
        "/hw",
        "/news",
        "/eat",
        "/template",
        "/ddl",
    }
    assert data["/news_block"] is False
    assert data["/news_reset"] is False
    assert data["/help"] is False


def test_plain_text_passes_through():
    assert command_prompt("今天有什么校园新闻") == "今天有什么校园新闻"
    assert command_prompt("") == ""


def test_unknown_command_passes_through():
    assert command_prompt("/unknown_cmd") == "/unknown_cmd"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/hw", "列出我的作业"),
        ("/hw list", "列出我的作业"),
        ("/hw do 3", "帮我分析第 3 个作业并生成解答"),
        ("/hw brief 3", "帮我生成第 3 个作业的摘要"),
        ("/hw due", "列出 3 天内截止的作业"),
        ("/hw due 7", "列出 7 天内截止的作业"),
        ("/hw past", "列出历史作业（包括已交的）"),
        ("/hw all", "列出所有作业（包括历史作业）"),
        ("/news", "今天有什么校园新闻"),
        ("/news_block 教务处", "屏蔽「教务处」类校园新闻"),
        ("/news_reset", "重置我的新闻偏好"),
        ("/eat", "推荐一下现在闵行校区去哪吃"),
        ("/eat 徐汇", "推荐一下现在徐汇校区去哪吃"),
        ("/template", "列出可用的 LaTeX 模板"),
        ("/template compile", "编译当前 LaTeX 模板生成 PDF"),
        ("/template push", "把当前论文目录推送到 Overleaf"),
        ("/template bachelor-thesis", "套用 LaTeX 模板：bachelor-thesis"),
        ("/ddl", "查看我的 DDL"),
        ("/help", "你能做什么？请介绍一下可用功能"),
    ],
)
def test_command_prompt_known_commands(command, expected):
    assert command_prompt(command) == expected


def test_command_prompt_hw_past_do():
    assert command_prompt("/hw past do 3") == "帮我分析历史作业中第 3 个并生成解答"


def test_command_prompt_hw_unknown_sub_falls_back_to_list():
    """与 Feishu _cmd_hw 行为一致：未知子命令回退为列作业。"""
    assert command_prompt("/hw wat") == "列出我的作业"


def test_command_prompt_news_block_without_category_asks():
    assert "屏蔽" in command_prompt("/news_block")
    assert "分类" in command_prompt("/news_block")


def test_command_prompt_eat_invalid_campus():
    prompt = command_prompt("/eat 伦敦")
    assert "伦敦" in prompt
    assert "闵行" in prompt


def test_command_prompt_template_clone_optional_name():
    assert command_prompt("/template clone 123") == "从 Overleaf 克隆项目 123 作为 LaTeX 模板"
    assert command_prompt("/template clone 123 论文") == (
        "从 Overleaf 克隆项目 123，命名为 论文，作为 LaTeX 模板"
    )
    assert "project-id" in command_prompt("/template clone")


def test_command_prompt_handles_uppercase_name():
    assert command_prompt("/EAT 徐汇") == "推荐一下现在徐汇校区去哪吃"


def test_every_example_maps_to_a_non_command_string():
    """命令面板给用户的示例都应该能被翻译成自然语言。"""
    for command in COMMANDS:
        for example in command.examples:
            resolved = command_prompt(example)
            assert resolved
            assert not resolved.startswith("/")
