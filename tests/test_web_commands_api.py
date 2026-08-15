from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from sjtu_agent.web import server as web_server


@pytest.fixture()
def web_httpd(monkeypatch):
    """Start a real _Handler HTTP server on an ephemeral port."""
    monkeypatch.setattr(web_server, "_WEB_TOKEN", "test-token")
    httpd = web_server.ThreadingHTTPServer(("127.0.0.1", 0), web_server._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _get(port: int, path: str, cookie: str = "sjtu_token=test-token"):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers={"Cookie": cookie})
        res = conn.getresponse()
        body = res.read().decode("utf-8")
        return res.status, json.loads(body) if body else None
    finally:
        conn.close()


def _post(port: int, path: str, payload: dict, cookie: str = "sjtu_token=test-token"):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            "POST",
            path,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Cookie": cookie, "Content-Type": "application/json"},
        )
        res = conn.getresponse()
        body = res.read().decode("utf-8")
        if not body:
            return res.status, None
        try:
            return res.status, json.loads(body)
        except json.JSONDecodeError:
            return res.status, body
    finally:
        conn.close()


def _sse_events(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload and payload != "[DONE]":
            events.append(json.loads(payload))
    return events


def test_commands_endpoint_requires_auth(web_httpd):
    status, _ = _get(web_httpd, "/api/commands", cookie="")
    assert status == 403


def test_commands_endpoint_returns_metadata(web_httpd):
    status, data = _get(web_httpd, "/api/commands")
    assert status == 200
    assert "commands" in data
    names = {item["name"] for item in data["commands"]}
    assert {"/hw", "/news", "/news_block", "/news_reset", "/eat", "/template", "/ddl"} <= names

    for item in data["commands"]:
        assert {"name", "label", "icon", "description", "prompt", "examples", "chip", "exec"} <= set(item)


def test_commands_resolve_endpoint_requires_auth(web_httpd):
    status, _ = _get(web_httpd, "/api/commands/resolve?text=/hw", cookie="")
    assert status == 403


def test_commands_resolve_endpoint_translates_command(web_httpd):
    status, data = _get(web_httpd, "/api/commands/resolve?text=/hw%20do%203")
    assert status == 200
    assert data == {"prompt": "帮我分析第 3 个作业并生成解答"}


def test_commands_resolve_endpoint_passes_plain_text_through(web_httpd):
    status, data = _get(web_httpd, "/api/commands/resolve?text=hello")
    assert status == 200
    assert data == {"prompt": "hello"}


def test_command_endpoint_rejects_unknown_command(web_httpd):
    status, data = _post(web_httpd, "/api/command", {"command": "/ddl"})
    assert status == 400
    assert data["error"] == "unknown command"
    assert "DDL" in data["prompt"]


def test_command_endpoint_streams_result_and_persists_session(web_httpd):
    _, session = _post(web_httpd, "/api/sessions", {"title": "命令测试"})
    session_id = session["id"]

    status, body = _post(
        web_httpd,
        "/api/command",
        {"command": "/eat 伦敦", "session_id": session_id},
    )
    assert status == 200

    events = _sse_events(body)
    assert events[0].get("command_start", {}).get("name") == "/eat"
    assert any("command_progress" in e for e in events)
    result_event = next(e["command_result"] for e in events if "command_result" in e)
    assert result_event["name"] == "/eat"
    assert result_event["view"] == "dining"
    assert result_event["data"]["mode"] == "invalid_campus"
    assert "未知校区" in result_event["text"]

    _, messages_data = _get(web_httpd, f"/api/sessions/{session_id}/messages")
    messages = messages_data["messages"]
    assert messages[-2]["role"] == "user"
    assert "/eat 伦敦" in messages[-2]["content"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"].startswith("__SJTU_COMMAND_RESULT__")
    assert "invalid_campus" in messages[-1]["content"]
