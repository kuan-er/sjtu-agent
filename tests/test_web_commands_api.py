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
        assert {"name", "label", "icon", "description", "prompt", "examples", "chip"} <= set(item)


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
