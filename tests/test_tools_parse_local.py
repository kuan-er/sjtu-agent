from __future__ import annotations

import tempfile
from pathlib import Path

from sjtu_agent.agent.tools import tool_parse_local_file


def _mk_local_tmpdir() -> Path:
    base = Path.cwd() / ".test_runtime_manual"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="tool_parse_test_", dir=str(base)))


def test_tool_parse_local_file_legacy_html():
    tmp_path = _mk_local_tmpdir()
    p = tmp_path / "a.html"
    p.write_text("<html><body><h1>T</h1><p>Body</p></body></html>", encoding="utf-8")

    r = tool_parse_local_file(str(p), max_chars=200, strategy="legacy")
    assert r.get("ok") is True
    assert r.get("parser") == "legacy_read_assignment_file"
    assert "Body" in r.get("content", "")


def test_tool_parse_local_file_auto_txt():
    tmp_path = _mk_local_tmpdir()
    p = tmp_path / "a.txt"
    p.write_text("router text", encoding="utf-8")

    r = tool_parse_local_file(str(p), max_chars=200, strategy="auto")
    assert r.get("ok") is True
    assert "router text" in r.get("content", "")
