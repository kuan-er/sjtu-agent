from __future__ import annotations

import tempfile
from pathlib import Path

from sjtu_agent.agent.tools import tool_parse_local_file
import sjtu_agent.agent.tools as tools_mod


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


def test_tool_parse_local_file_missing_ocr_install_and_retry(monkeypatch):
    tmp_path = _mk_local_tmpdir()
    p = tmp_path / "a.png"
    p.write_bytes(b"fake")

    calls = {"count": 0}

    def _fake_parse_router(file_path: str, max_chars: int, start_page: int, strategy: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "ok": True,
                "parser": "image_stub",
                "content": "[image] a.png\nOCR backend missing: install paddleocr to extract text.",
                "warnings": ["OCR backend missing: install paddleocr to extract text."],
            }
        return {"ok": True, "parser": "paddleocr", "content": "recognized text", "warnings": []}

    monkeypatch.setattr(tools_mod, "parse_router_file", _fake_parse_router)
    monkeypatch.setattr(tools_mod, "_is_interactive_chat_for_install_prompt", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    monkeypatch.setattr(tools_mod, "_install_missing_backend_package", lambda backend: (True, ""))

    r = tool_parse_local_file(str(p), max_chars=200, strategy="auto")
    assert calls["count"] == 2
    assert r.get("ok") is True
    assert r.get("parser") == "paddleocr"
    assert "recognized text" in r.get("content", "")
    assert "auto_installed:paddleocr" in (r.get("warnings") or [])


def test_tool_parse_local_file_missing_asr_install_declined(monkeypatch):
    tmp_path = _mk_local_tmpdir()
    p = tmp_path / "a.wav"
    p.write_bytes(b"fake")

    def _fake_parse_router(file_path: str, max_chars: int, start_page: int, strategy: str):
        return {
            "ok": True,
            "parser": "audio_stub",
            "content": "[audio] a.wav\nASR backend missing: install whisper for transcription.",
            "warnings": ["ASR backend missing: install whisper for transcription."],
        }

    monkeypatch.setattr(tools_mod, "parse_router_file", _fake_parse_router)
    monkeypatch.setattr(tools_mod, "_is_interactive_chat_for_install_prompt", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    monkeypatch.setattr(
        tools_mod,
        "_install_missing_backend_package",
        lambda backend: (_ for _ in ()).throw(AssertionError("should not install when user declined")),
    )

    r = tool_parse_local_file(str(p), max_chars=200, strategy="auto")
    assert r.get("ok") is True
    assert r.get("parser") == "audio_stub"
    assert "install_skipped:whisper" in (r.get("warnings") or [])


def test_detect_missing_parse_backend_pdf_warning():
    parsed = {
        "ok": True,
        "parser": "pypdf",
        "content": "",
        "warnings": ["PDF OCR backend missing: install paddleocr and pypdfium2, then retry."],
    }
    assert tools_mod._detect_missing_parse_backend(parsed) == "pdf_ocr"


def test_tool_install_parse_backend_ok(monkeypatch):
    monkeypatch.setattr(tools_mod, "_install_missing_backend_package", lambda backend: (True, ""))
    r = tools_mod.tool_install_parse_backend("pdf_ocr")
    assert r.get("ok") is True
    assert r.get("backend") == "pdf_ocr"
