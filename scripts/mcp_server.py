#!/usr/bin/env python3
"""
SJTU DDL Checker MCP Server

暴露三个工具供 AI Agent 调用：
  - get_ddls       : 获取所有平台未完成的 DDL 列表
  - get_next_lab   : 获取下一次物理实验安排
  - get_all        : 同时获取 DDL 和实验安排（汇总）

启动方式：
  stdio 模式（Claude Desktop）：
    python3 mcp_server.py

  HTTP/SSE 模式（ChatGPT Desktop，端口默认 8765）：
    python3 mcp_server.py --http [--port 8765]

Claude Desktop 配置 (~/.claude_desktop_config.json)：
  {
    "mcpServers": {
      "sjtu-ddl": {
        "command": "python3",
        "args": ["/path/to/sjtu-agent/mcp_server.py"]
      }
    }
  }

ChatGPT Desktop：
  1. 先在终端运行：python3 mcp_server.py --http
  2. 在 ChatGPT Settings → Connectors/Apps 添加 URL：
     http://localhost:8765/sse
"""

import sys
from pathlib import Path

# 确保能 import 同目录下的 ddl_checker
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP
import ddl_checker as dc

try:  # 在 instructions 里带上产品版本：FastMCP 的 serverInfo.version 是 mcp SDK 自己的版本
    from sjtu_agent import __version__ as _sjtu_version
except Exception:  # noqa: BLE001 — 极端情况下（未安装包）退回占位
    _sjtu_version = "unknown"

mcp = FastMCP(
    "sjtu-ddl",
    instructions=(
        "上海交通大学校园助手 sjtu-agent 的 DDL / 实验查询服务"
        f"（sjtu-agent v{_sjtu_version}）：汇总 Canvas、中国大学 MOOC 等平台的未完成作业，"
        "以及下一次物理实验安排。"
    ),
)


def _serialize_ddl(item: dict) -> dict:
    """将 DDL 条目序列化为 JSON 安全的 dict。"""
    return {
        "platform": item["platform"],
        "course":   item["course"],
        "name":     item["name"],
        "due":      item["due"].isoformat(),
        "days_left": (item["due"] - dc.NOW).days,
        "submitted": item.get("submitted", False),
    }


def _serialize_lab(lab: dict | None) -> dict | None:
    if not lab:
        return None
    dt = lab["dt"]
    return {
        "name":     lab["name"],
        "datetime": dt.isoformat(),
        "weekday":  dc.WEEKDAY_ZH[dt.weekday()],
        "time_str": lab["time_str"],
        "room":     lab["room"],
    }


@mcp.tool()
def get_ddls(
    skip_canvas:  bool = False,
    skip_aihaoke: bool = False,
    skip_icourse: bool = False,
) -> list[dict]:
    """
    获取所有平台未完成的 DDL 列表，按截止时间升序排列。

    参数：
      skip_canvas   : 跳过 Canvas
      skip_aihaoke  : 跳过 aihaoke
      skip_icourse  : 跳过中国大学 MOOC (icourse163)

    返回每条 DDL 的字段：
      platform  : 平台名 (canvas / aihaoke / icourse163)
      course    : 课程名称
      name      : 作业/测验名称
      due       : 截止时间 (ISO 8601)
      days_left : 距截止还有几天（负数表示已过期）
      submitted : 是否已提交
    """
    cfg = dc.load_config()
    all_ddl: list[dict] = []

    if not skip_canvas:
        all_ddl.extend(dc.fetch_canvas(cfg))
    if not skip_aihaoke:
        all_ddl.extend(dc.fetch_aihaoke(cfg))
    if not skip_icourse:
        all_ddl.extend(dc.fetch_icourse(cfg))

    all_ddl.sort(key=lambda x: x["due"])
    pending = [x for x in all_ddl if not x.get("submitted")]
    return [_serialize_ddl(x) for x in pending]


@mcp.tool()
def get_next_lab() -> dict | None:
    """
    获取下一次物理实验安排。

    返回字段：
      name      : 实验项目名称
      datetime  : 实验时间 (ISO 8601)
      weekday   : 星期几（中文）
      time_str  : 时间字符串（如 "星期一18:00"）
      room      : 实验地点

    若无法获取则返回 null。
    """
    cfg = dc.load_config()
    lab = dc.fetch_phycai(cfg)
    return _serialize_lab(lab)


@mcp.tool()
def get_all(
    skip_canvas:  bool = False,
    skip_aihaoke: bool = False,
    skip_icourse: bool = False,
    skip_phycai:  bool = False,
) -> dict:
    """
    一次性获取所有 DDL 和物理实验安排。

    返回：
      ddls : DDL 列表（同 get_ddls 格式）
      lab  : 下一次物理实验（同 get_next_lab 格式，无则为 null）
    """
    cfg = dc.load_config()
    all_ddl: list[dict] = []

    if not skip_canvas:
        all_ddl.extend(dc.fetch_canvas(cfg))
    if not skip_aihaoke:
        all_ddl.extend(dc.fetch_aihaoke(cfg))
    if not skip_icourse:
        all_ddl.extend(dc.fetch_icourse(cfg))

    all_ddl.sort(key=lambda x: x["due"])
    pending = [x for x in all_ddl if not x.get("submitted")]

    lab = None
    if not skip_phycai:
        lab = dc.fetch_phycai(cfg)

    return {
        "ddls": [_serialize_ddl(x) for x in pending],
        "lab":  _serialize_lab(lab),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="以 HTTP/SSE 模式启动（供 ChatGPT Desktop 使用）")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 模式端口（默认 8765）")
    args = parser.parse_args()

    if args.http:
        print(f"启动 HTTP/SSE 模式，监听 http://localhost:{args.port}/sse")
        mcp.settings.port = args.port
        mcp.settings.host = "127.0.0.1"
        mcp.run(transport="sse")
    else:
        mcp.run()
