"""tests/test_cli_passthrough.py — passthrough 子命令的透传契约。

背景：`sjtu-agent feishu-bot --test` 这类把 option 形参数透传给脚本的
用法依赖 main() 的 parse_known_args + unknown 合并逻辑（REMAINDER 本身
吞不掉 `-` 开头参数）。这些测试锁住该契约，防止将来重构时退化——
文档（docs/feishu-bot-troubleshooting.md 等）大量依赖 `--test` / `--whoami`
不带 `--` 分隔符的直接写法。
"""

import pytest


def _run_passthrough(monkeypatch, argv):
    """用 spy 替换 handler，返回 (rc, script_args)；模拟 cli.main(argv)。"""
    from sjtu_agent import cli

    seen = {}

    def spy(args):
        seen["script_args"] = list(getattr(args, "script_args", None) or [])
        return 0

    monkeypatch.setattr(cli, "_cmd_feishu_bot", spy)
    rc = cli.main(argv)
    return rc, seen.get("script_args", [])


def test_feishu_bot_test_flag_forwarded(monkeypatch):
    rc, args = _run_passthrough(monkeypatch, ["feishu-bot", "--test"])
    assert rc == 0
    assert args == ["--test"]


def test_feishu_bot_whoami_flag_forwarded(monkeypatch):
    rc, args = _run_passthrough(monkeypatch, ["feishu-bot", "--whoami"])
    assert rc == 0
    assert args == ["--whoami"]


def test_feishu_bot_no_args_forwarded(monkeypatch):
    rc, args = _run_passthrough(monkeypatch, ["feishu-bot"])
    assert rc == 0
    assert args == []


def test_feishu_bot_plain_positionals_forwarded(monkeypatch):
    rc, args = _run_passthrough(monkeypatch, ["feishu-bot", "extra", "pos"])
    assert rc == 0
    assert args == ["extra", "pos"]


def test_feishu_bot_mixed_flags_and_positionals(monkeypatch):
    rc, args = _run_passthrough(monkeypatch, ["feishu-bot", "--test", "extra"])
    assert rc == 0
    # 透传契约只保证参数都送达，不保证相对顺序（positionals 由 REMAINDER 先收，
    # option 形剩余参数随后合并追加）
    assert "--test" in args and "extra" in args


def test_non_passthrough_unknown_args_still_error(monkeypatch, capsys):
    """非 passthrough 子命令收到未知参数仍应报错（不能被静默吞掉）。"""
    from sjtu_agent import cli

    # `doctor` 是普通子解析器（无 script_args），未知参数应触发 usage 错误
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", "--bogus-flag"])
    assert exc.value.code == 2


def test_bare_invocation_falls_back_to_chat(monkeypatch):
    """裸 `sjtu-agent` 走 chat 兜底（script_args=[]）。"""
    from sjtu_agent import cli

    seen = {}

    def spy_chat(args):
        seen["script_args"] = list(getattr(args, "script_args", None) or [])
        return 0

    monkeypatch.setattr(cli, "_cmd_chat", spy_chat)
    rc = cli.main([])
    assert rc == 0
    assert seen["script_args"] == []