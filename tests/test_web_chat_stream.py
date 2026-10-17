"""Web/TUI 共享聊天流：图片原生发送与「被拒后回退文本」路径。"""

from __future__ import annotations

import json

from sjtu_agent.web import server as web_server


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _png_item(tmp_path) -> dict:
    path = tmp_path / "photo.png"
    path.write_bytes(PNG)
    return {
        "id": "a1",
        "session_id": None,
        "filename": "photo.png",
        "path": str(path),
        "mime_type": "image/png",
        "size": len(PNG),
    }


def _errors(chunks: list[str]) -> list[str]:
    errors = []
    for chunk in chunks:
        if not chunk.startswith("data: "):
            continue
        payload = chunk[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        obj = json.loads(payload)
        if "error" in obj:
            errors.append(obj["error"])
    return errors


def test_image_is_sent_natively_when_model_can_see(monkeypatch, tmp_path):
    seen: list[dict] = []

    def fake_stream(client, model, agent, rounds, sse, history, cancel_key):
        seen.append(json.loads(json.dumps(history[-1])))
        yield 'data: {"token": "看到了"}\n\n'

    monkeypatch.setattr(web_server, "_get_chat_client", lambda: (object(), "gpt-6.1-sol", "openai"))
    monkeypatch.setattr(web_server, "_stream_chat_openai", fake_stream)
    monkeypatch.setattr(
        "sjtu_agent.vision_capability.resolve_vision_capability", lambda model, client=None: True
    )

    chunks = list(web_server._stream_chat("这是什么", attachment_items=[_png_item(tmp_path)]))

    assert not _errors(chunks)
    content = seen[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "已随本条消息附上" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_falls_back_to_text_when_model_cannot_see(monkeypatch, tmp_path):
    seen: list[dict] = []

    def fake_stream(client, model, agent, rounds, sse, history, cancel_key):
        seen.append(json.loads(json.dumps(history[-1])))
        yield 'data: {"token": "从文字里读到了"}\n\n'

    monkeypatch.setattr(web_server, "_get_chat_client", lambda: (object(), "deepseek-chat", "openai"))
    monkeypatch.setattr(web_server, "_stream_chat_openai", fake_stream)
    monkeypatch.setattr(
        "sjtu_agent.vision_capability.resolve_vision_capability", lambda model, client=None: False
    )
    monkeypatch.setattr(web_server, "_attachment_note_for_chat", lambda items: "\n\n[图片文字：课表]")

    chunks = list(web_server._stream_chat("这是什么", attachment_items=[_png_item(tmp_path)]))

    assert not _errors(chunks)
    content = seen[0]["content"]
    assert isinstance(content, str)
    assert "不能直接读图" in content
    assert "[图片文字：课表]" in content


def test_rejection_mid_stream_retries_with_text(monkeypatch, tmp_path):
    """端点声称能识图、实际拒收：不把错误抛给用户，改用文本路径重试一次。"""
    calls: list[dict] = []

    def fake_stream(client, model, agent, rounds, sse, history, cancel_key):
        calls.append(json.loads(json.dumps(history[-1])))
        if len(calls) == 1:
            yield 'data: {"error": "400 unsupported content type: image_url"}\n\n'
            return
        yield 'data: {"token": "重试成功"}\n\n'

    recorded: list[str] = []
    monkeypatch.setattr(web_server, "_get_chat_client", lambda: (object(), "new-model-x", "openai"))
    monkeypatch.setattr(web_server, "_stream_chat_openai", fake_stream)
    monkeypatch.setattr(
        "sjtu_agent.vision_capability.resolve_vision_capability", lambda model, client=None: True
    )
    monkeypatch.setattr(
        "sjtu_agent.vision_capability.record_vision_unsupported", recorded.append
    )
    monkeypatch.setattr(web_server, "_attachment_note_for_chat", lambda items: "\n\n[图片文字：课表]")

    chunks = list(web_server._stream_chat("这是什么", attachment_items=[_png_item(tmp_path)]))

    assert not _errors(chunks)          # 拒绝错误没有发给前端
    assert len(calls) == 2
    assert recorded == ["new-model-x"]  # 记成「不吃图片」，下次不再撞
    assert isinstance(calls[0]["content"], list)
    assert isinstance(calls[1]["content"], str)
    assert "[图片文字：课表]" in calls[1]["content"]


def test_non_image_attachment_keeps_text_path(monkeypatch, tmp_path):
    seen: list[dict] = []

    def fake_stream(client, model, agent, rounds, sse, history, cancel_key):
        seen.append(json.loads(json.dumps(history[-1])))
        yield 'data: {"token": "ok"}\n\n'

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    item = {
        "id": "p1",
        "filename": "a.pdf",
        "path": str(pdf),
        "mime_type": "application/pdf",
        "size": 8,
    }
    monkeypatch.setattr(web_server, "_get_chat_client", lambda: (object(), "gpt-6.1-sol", "openai"))
    monkeypatch.setattr(web_server, "_stream_chat_openai", fake_stream)
    monkeypatch.setattr(web_server, "_attachment_note_for_chat", lambda items: "\n\n[PDF 内容]")

    list(web_server._stream_chat("总结一下", attachment_items=[item]))

    content = seen[0]["content"]
    assert isinstance(content, str)
    assert "[PDF 内容]" in content
