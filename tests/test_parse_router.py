from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from sjtu_agent.parsing.router import parse_file, parse_files


def _mk_local_tmpdir() -> Path:
    base = Path.cwd() / ".test_runtime_manual"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="parse_test_", dir=str(base)))


def test_parse_file_text():
    tmp_path = _mk_local_tmpdir()
    p = tmp_path / "a.txt"
    p.write_text("hello\nworld", encoding="utf-8")

    r = parse_file(str(p), max_chars=100)
    assert r["ok"] is True
    assert r["parser"] == "text"
    assert "hello" in r["content"]


def test_parse_files_merge():
    tmp_path = _mk_local_tmpdir()
    p1 = tmp_path / "a.txt"
    p2 = tmp_path / "b.txt"
    p1.write_text("alpha", encoding="utf-8")
    p2.write_text("beta", encoding="utf-8")

    r = parse_files([str(p1), str(p2)], per_file_max_chars=100, total_max_chars=1000)
    assert r["ok"] is True
    assert r["success_count"] == 2
    assert "alpha" in r["content"]
    assert "beta" in r["content"]


def test_parse_file_missing():
    r = parse_file("D:/__does_not_exist__.txt")
    assert r["ok"] is False
    assert "文件不存在" in r["error"]


def test_parse_pptx_xml():
    tmp_path = _mk_local_tmpdir()
    p = tmp_path / "slides.pptx"
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp><p:txBody><a:p><a:r><a:t>Hello PPTX</a:t></a:r></a:p></p:txBody></p:sp>
      <p:sp><p:txBody><a:p><a:r><a:t>Second line</a:t></a:r></a:p></p:txBody></p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>""",
        )

    r = parse_file(str(p), max_chars=1000)
    assert r["ok"] is True
    assert r["parser"] == "pptx_xml"
    assert "Hello PPTX" in r["content"]
