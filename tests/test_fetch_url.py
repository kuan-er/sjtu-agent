from __future__ import annotations

import socket

import pytest
import requests
from bs4 import BeautifulSoup

from sjtu_agent.agent.tools import _core


class _Resp:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """单元测试不依赖真实 DNS：默认把域名解析到公网地址。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )


@pytest.fixture(autouse=True)
def _no_ambient_proxy(monkeypatch):
    """清掉环境里的代理变量，避免"配了代理就跳过 DNS 校验"干扰这些用例。"""
    from sjtu_agent.agent.tools import _web_common

    for name in _web_common._PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _resolve_to(addrs):
    return lambda host, port, *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in addrs
    ]


# ── SSRF 校验 ────────────────────────────────────────────────────────────────


def test_rejects_private_ip_literal():
    assert _core._validate_fetch_url("http://127.0.0.1/admin") is not None
    assert _core._validate_fetch_url("http://10.1.2.3/") is not None
    assert _core._validate_fetch_url("http://169.254.169.254/latest/meta-data") is not None
    assert _core._validate_fetch_url("file:///etc/passwd") is not None


def test_rejects_hostname_resolving_to_private(monkeypatch):
    """公网域名指向内网 IP 的绕过必须被拦住。"""
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(["10.0.0.5"]))
    err = _core._validate_fetch_url("http://internal.example.com/secret")
    assert err is not None
    assert "内网" in err["error"]


def test_allows_fake_ip_dns_range(monkeypatch):
    """回归：Clash 等 fake-ip DNS 会把域名解析到 198.18.0.0/15。

    Python 的 ipaddress 把这一段算作 is_private，若照此拦截，开了 fake-ip 的用户
    会连正常网页都抓不到（实测本机 www.bing.com → 198.18.0.47）。
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(["198.18.0.47"]))
    assert _core._validate_fetch_url("https://www.bing.com/") is None


def test_proxy_configured_skips_dns_check(monkeypatch):
    """配了代理时，请求由代理转发，本机 DNS 结果不代表真正连去哪儿。"""
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(["10.0.0.5"]))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    assert _core._validate_fetch_url("https://www.bing.com/") is None
    # 但字面内网 IP 仍然拦截（URL 本身就指向内网，与代理无关）
    assert _core._validate_fetch_url("http://10.0.0.5/admin") is not None


def test_allows_hostname_resolving_to_public(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    assert _core._validate_fetch_url("https://example.com/") is None


def test_redirect_hop_to_private_is_blocked(monkeypatch):
    """requests 自动跟随重定向会绕过校验，因此必须逐跳校验。"""

    def fake_get(url, **kwargs):
        return _Resp(status_code=302, headers={"Location": "http://192.168.1.1/secret"})

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(ValueError):
        _core._fetch_html("https://example.com/", _core._GENERAL_FETCH_HEADERS)


# ── 正文提取 ─────────────────────────────────────────────────────────────────


def test_extract_main_text_skips_navigation():
    html = (
        "<html><body>"
        "<nav><a href='/a'>首页</a><a href='/b'>新闻</a></nav>"
        "<article>" + "<p>这是正文段落，讲的是课程安排与作业要求。</p>" * 12 + "</article>"
        "<footer>版权所有</footer>"
        "</body></html>"
    )
    text = _core._extract_main_text(BeautifulSoup(html, "html.parser"))
    assert "这是正文段落" in text
    assert "首页" not in text
    assert "版权所有" not in text


def test_extract_main_text_falls_back_to_full_text():
    html = "<html><body><div><span>只有一小段文字。</span></div></body></html>"
    text = _core._extract_main_text(BeautifulSoup(html, "html.parser"))
    assert "只有一小段文字" in text


# ── 抓取行为 ─────────────────────────────────────────────────────────────────


def test_fetch_url_uses_general_headers_for_normal_sites(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, **kwargs):
        seen["url"] = url
        seen["headers"] = headers or {}
        return _Resp(
            text="<html><head><title>标题</title></head><body><article>"
            + "<p>正文内容，描述这次讲座的时间与地点。</p>" * 12
            + "</article></body></html>"
        )

    monkeypatch.setattr(requests, "get", fake_get)
    result = _core.tool_fetch_url("https://news.sjtu.edu.cn/post/1")
    assert result["ok"] is True
    assert result["title"] == "标题"
    assert "正文内容" in result["content"]
    # 普通站点不该再用微信 UA / 微信 Referer
    assert "MicroMessenger" not in seen["headers"]["User-Agent"]
    assert "Referer" not in seen["headers"]


def test_fetch_url_follows_public_redirect(monkeypatch):
    hops: list[str] = []

    def fake_get(url, **kwargs):
        hops.append(url)
        if len(hops) == 1:
            return _Resp(status_code=301, headers={"Location": "/final"})
        return _Resp(text="<html><body><article>" + "<p>最终页面正文。</p>" * 12 + "</article></body></html>")

    monkeypatch.setattr(requests, "get", fake_get)
    result = _core.tool_fetch_url("https://example.com/start")
    assert result["ok"] is True
    assert hops == ["https://example.com/start", "https://example.com/final"]


def test_fetch_url_reports_http_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: _Resp(status_code=404))
    result = _core.tool_fetch_url("https://example.com/missing")
    assert result["ok"] is False
    assert "抓取失败" in result["error"]


# ── 编码与正文质量（issue #204 复现三、四） ──────────────────────────────────


class _EncodedResp:
    """模仿 requests：content 是字节，text 按 self.encoding 解码。"""

    def __init__(self, content: bytes, headers: dict | None = None):
        self.content = content
        self.status_code = 200
        self.headers = headers or {}
        # 与 requests 一致：头里有 charset 就用它，否则 text/html 默认 ISO-8859-1
        content_type = str(self.headers.get("content-type") or "").lower()
        if "charset=" in content_type:
            self.encoding = content_type.split("charset=")[-1].split(";")[0].strip()
        else:
            self.encoding = "ISO-8859-1"
        self.apparent_encoding = "utf-8"

    @property
    def text(self):
        return self.content.decode(self.encoding, "replace")

    def raise_for_status(self):
        return None


def test_decode_response_uses_html_charset_when_header_lacks_it():
    """HTTP 头没写 charset、HTML 里声明 UTF-8 时不能按 ISO-8859-1 解出乱码。"""
    from sjtu_agent.agent.tools._web_common import decode_response

    body = (
        '<html><head><meta charset="utf-8"><title>上海交通大学图书馆</title>'
        "</head><body><p>开放时间</p></body></html>"
    ).encode("utf-8")
    resp = _EncodedResp(body, headers={"content-type": "text/html"})
    assert "上海交通大学图书馆" in decode_response(resp)


def test_decode_response_respects_header_charset():
    from sjtu_agent.agent.tools._web_common import decode_response

    body = "<html><body>测试</body></html>".encode("gbk")
    resp = _EncodedResp(body, headers={"content-type": "text/html; charset=gbk"})
    assert "测试" in decode_response(resp)


def test_fetch_url_flags_title_only_shell(monkeypatch):
    """图书馆首页那种只有标题的动态外壳：ok 但必须带 quality 与 warning。"""
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, **kwargs: _Resp(
            text='<html><head><title>上海交通大学图书馆</title></head>'
            '<body><div id="htContainer"></div><script src="/assets/js/v2.js"></script>'
            "</body></html>"
        ),
    )
    result = _core.tool_fetch_url("https://www.lib.sjtu.edu.cn/")
    assert result["ok"] is True
    assert result["quality"] == "title_only"
    assert result["short"] is True
    assert "动态渲染" in result["warning"]


def test_fetch_url_full_article_has_ok_quality(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, **kwargs: _Resp(
            text="<html><head><title>讲座通知</title></head><body><article>"
            + "<p>本次讲座于周五下午在闵行校区图书馆报告厅举行，欢迎参加。</p>" * 6
            + "</article></body></html>"
        ),
    )
    result = _core.tool_fetch_url("https://news.sjtu.edu.cn/post/2")
    assert result["ok"] is True
    assert result["quality"] == "ok"
    assert "warning" not in result
