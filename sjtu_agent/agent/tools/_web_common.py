"""
sjtu_agent/agent/tools/_web_common.py — 网页抓取与正文提取的公共实现。

`fetch_url` 工具与「搜索即阅读」流水线（web_search 的 read_top）共用这里的代码：
- URL 安全校验（防 SSRF：字面 IP + **域名解析结果**）
- 逐跳跟随重定向的 GET（requests 自动跟随会绕过校验）
- 正文提取：按「段落字数 × (1 − 链接密度)」挑容器，保留段落结构

本模块只依赖标准库 / requests / bs4，不 import 其它 tools 子模块，避免循环依赖。
"""

from __future__ import annotations

import ipaddress
import os
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
GENERAL_FETCH_HEADERS = {
    "User-Agent": _DESKTOP_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
WEIXIN_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "MicroMessenger/8.0.43(0x18002b2d) NetType/WIFI Language/zh_CN"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://mp.weixin.qq.com/",
}

TRUNCATE_MARKER = "\n\n[内容过长，已截断]"


# 明确的内网/保留网段。
#
# 刻意**不用** `ipaddress.is_private`：它把 198.18.0.0/15（IANA 基准测试段）也算作
# private，而 Clash / Surge 等代理的 fake-ip DNS 正是把**所有**域名解析到这一段。
# 若照 is_private 拦截，开了 fake-ip 的用户（国内代理场景极常见）会连正常网页都
# 抓不到——实测本机 www.bing.com → 198.18.0.47、news.qq.com → 198.18.1.59。
_INTERNAL_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # 含云元数据地址 169.254.169.254
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

_PROXY_ENV_VARS = (
    "SJTU_WEB_SEARCH_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def proxy_configured() -> bool:
    """是否配了（任何）代理——决定请求由本机直连还是交给代理转发。"""
    return any(os.environ.get(name, "").strip() for name in _PROXY_ENV_VARS)


def _is_private_address(addr) -> bool:
    return any(net.version == addr.version and addr in net for net in _INTERNAL_NETS)


def resolves_to_private(host: str) -> bool:
    """域名解析后是否落在内网地址——防「公网域名指向 10.x」的 SSRF 绕过。"""
    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # 解析不了就交给 requests 去失败，不在这里误判
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_private_address(addr):
            return True
    return False


def validate_public_url(url: str) -> dict | None:
    """校验 URL 可安全抓取：仅 http(s)，且不指向内网地址。

    - 字面 IP：直接按内网网段判定（与代理无关，URL 本身就指向内网）。
    - 域名：看解析结果；但**配了代理时不看**——请求由代理转发，本机 DNS
      可能只是 fake-ip 占位地址，与真正连去哪儿无关。

    Returns an error dict if invalid, None if OK.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"不支持的协议: {parsed.scheme}，仅允许 http/https"}
    host = parsed.hostname
    if not host:
        return {"ok": False, "error": "无法解析 URL 主机名"}
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if not proxy_configured() and resolves_to_private(host):
            return {"ok": False, "error": "该域名解析到内网地址，已拒绝访问"}
    else:
        if _is_private_address(addr):
            return {"ok": False, "error": "不允许访问内网地址"}
    return None


def fetch_html(url: str, headers: dict, timeout: float = 30, max_redirects: int = 4):
    """手动逐跳跟随重定向，每一跳都过 SSRF 校验。

    requests 默认自动跟随重定向，等于把校验过的安全 URL 换成未校验的新地址，
    因此这里显式 allow_redirects=False 自己走。
    """
    current = url
    for _ in range(max_redirects + 1):
        resp = requests.get(
            current, headers=headers, timeout=timeout, allow_redirects=False
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location") or ""
            if not location:
                return resp
            target = urllib.parse.urljoin(current, location)
            err = validate_public_url(target)
            if err:
                raise ValueError(f"重定向目标不允许：{err['error']}")
            current = target
            continue
        return resp
    raise ValueError("重定向次数过多，已中止")


def extract_title(soup, url: str = "") -> str:
    if "mp.weixin.qq.com" in url:
        tag = soup.find("h1", class_="rich_media_title") or soup.find(
            "h2", class_="rich_media_title"
        )
        if tag:
            return tag.get_text(strip=True)
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    if soup.find("h1"):
        return soup.find("h1").get_text(strip=True)
    return ""


def extract_main_text(soup) -> str:
    """从页面里挑出正文容器并保留段落结构。

    做法：清掉脚本/导航等噪声后，在 article/main/div/section 里按
    「段落总字数 × (1 − 链接密度)」打分，取分数最高者；提取结果过短就
    退回全文——避免抽到导航菜单或只抽到半句话。
    """
    for tag in soup(
        ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg"]
    ):
        tag.decompose()

    best = None
    best_score = 0.0
    for node in soup.find_all(["article", "main", "div", "section"]):
        paragraphs = node.find_all("p")
        text_len = sum(len(p.get_text(strip=True)) for p in paragraphs)
        if text_len < 120:
            continue
        total = len(node.get_text(strip=True)) or 1
        link_len = sum(len(a.get_text(strip=True)) for a in node.find_all("a"))
        score = text_len * (1.0 - min(link_len / total, 0.9))
        if score > best_score:
            best, best_score = node, score

    if best is None:
        return re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n", strip=True)).strip()

    chunks: list[str] = []
    for element in best.find_all(["h1", "h2", "h3", "h4", "p", "blockquote", "pre", "li"]):
        text = element.get_text(" ", strip=True)
        if len(text) < 2 or (chunks and chunks[-1] == text):
            continue
        chunks.append(text)
    text = "\n".join(chunks)
    if len(text) < 200:  # 正文容器判断失败 → 退回全文
        text = soup.get_text("\n", strip=True)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract_article_text(html_text: str, url: str = "") -> str:
    """HTML → 正文（微信文章走固定容器，其余走密度启发式）。"""
    soup = BeautifulSoup(html_text, "html.parser")
    if "mp.weixin.qq.com" in url:
        node = soup.find("div", id="js_content") or soup.find(
            "div", class_="rich_media_content"
        )
        if node:
            return re.sub(
                r"\n\s*\n+", "\n\n", node.get_text(separator="\n", strip=True)
            ).strip()
    return extract_main_text(soup)


def fetch_article_text(
    url: str,
    *,
    timeout: float = 15,
    max_chars: int = 1500,
    validate: bool = True,
) -> dict:
    """抓取单页正文。

    返回 {"ok": True, "url", "title", "text", "truncated"} 或
    {"ok": False, "url", "error"}——失败是结构化的，调用方自行决定降级。
    """
    if validate:
        err = validate_public_url(url)
        if err:
            return {"ok": False, "url": url, "error": err.get("error", "URL 不允许访问")}

    headers = (
        WEIXIN_FETCH_HEADERS if "mp.weixin.qq.com" in url else GENERAL_FETCH_HEADERS
    )
    try:
        resp = fetch_html(url, headers, timeout=timeout)
        resp.raise_for_status()
        text = extract_article_text(resp.text, url)
    except Exception as exc:  # noqa: BLE001 — 结构化返回，调用方决定是否降级
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"[:160]}

    if not text:
        return {"ok": False, "url": url, "error": "页面没有可提取的正文"}

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip() + TRUNCATE_MARKER
    soup_title = ""
    try:
        soup_title = extract_title(BeautifulSoup(resp.text, "html.parser"), url)
    except Exception:  # noqa: BLE001 — 标题失败不影响正文
        soup_title = ""
    return {
        "ok": True,
        "url": url,
        "title": soup_title,
        "text": text,
        "truncated": truncated,
    }
