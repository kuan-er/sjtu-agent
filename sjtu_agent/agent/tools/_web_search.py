"""
sjtu_agent/agent/tools/_web_search.py — 公开网页搜索工具（免 API Key，多引擎）。

在抓取栈之前还有一层 **DeepSeek 官方服务端搜索**（`_web_search_official.py`）：
配了 DeepSeek 官方 Key（`DEEPSEEK_API_KEY`）时优先走官方（结构化结果、无反爬与改版问题，
但一次搜索消耗一个模型轮次），失败或未配置则自动回落到下面的抓取栈；
`SJTU_WEB_SEARCH_BACKEND=auto|scrapers|deepseek` 可控。

三条独立抓取通路，按「结果质量 + 稳定性」排序：

1. **Bing RSS**（主）`https://www.bing.com/search?q=…&format=rss` —— 结构化 XML，
   天然给出**真实 URL** 与干净摘要，不受搜索结果页改版影响。
2. **Bing HTML**（备）解析 `<li class="b_algo">`；链接是 `ck/a` 跳转壳，
   从其中的 `u=a1<base64url>` 还原真实 URL（实测 6/6 可还原）。
3. **360 搜索**（中文兜底）真实地址写在 `data-mdurl`，摘要常带发布日期。

**升级条件是「质量」而不只是「条数」**：Bing 常常一口气给满 8 条品牌官网 /
百科，条数够但一个「封号」「风评」都没覆盖——这种情况按 `_is_thin()` 判定为
没打到问题焦点，继续换来源（360）与换关键词形态（缩写 / 意图限定词），
最后按焦点覆盖度排序，避免官网霸榜。

统一流程：还原真实 URL → 丢弃跳转壳 / 空壳 → 去重 → 同域限流 → 排序 → 交给模型。
搜索失败时返回结构化错误 + hint，不把异常原文丢给模型。

**搜索即阅读（read_top）**：把排在最前、且判定需要正文的几条**并发**抓回正文
（单页 1500 字、整轮 4000 字上限），摘要普遍偏短时自动开启；模型一次调用就能
拿到可作答的材料，省掉「先搜再 fetch_url」的一轮往返。抓取与提取复用
`_web_common`（与 fetch_url 同一套 SSRF 校验和正文提取）。

（DuckDuckGo 已移除：lite 端点返回 202 反爬页、解析恒为空，且大陆网络不可达。）
"""

from __future__ import annotations

import base64
import html
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

import requests

from sjtu_agent.agent.tools import _web_search_official as official
from sjtu_agent.agent.tools._web_common import fetch_article_text

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "联网搜索公开网页，返回标题、**真实链接**、摘要与来源域名。"
                "当用户提到你不认识的缩写、黑话、产品名、人物、事件，"
                "或问题涉及知识截止后的时效性信息时，必须先调用本工具，"
                "不得凭空猜测或假装知道。"
                "若返回结果全是官网/百科这类泛泛页面（没打到「评价、讨论、报错」语域），"
                "说明关键词不对，换更具体的词再搜一次。"
                "需要正文细节（原话、日期、步骤、数据）时把 read_top 设为 2-3，"
                "本工具会顺手抓回前几条的正文，比再调一次 fetch_url 省一轮。"
                "若结果里出现 degraded 字段，说明有来源被限流/拦截，"
                "此时结果可能偏官方，换更具体的词或改用 search_campus。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，尽量保留用户原始说法",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回多少条结果，默认 5，最大 8",
                    },
                    "read_top": {
                        "type": "integer",
                        "description": (
                            "可选：顺便抓取前几条结果的正文（0-3）。默认自动——"
                            "摘要普遍偏短时抓前 2 条；传 0 关闭，传 2/3 主动抓取。"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    }
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}

_TAG_RE = re.compile(r"<[^>]+>")
_BING_REDIRECT_RE = re.compile(r"[?&]u=a1([A-Za-z0-9_\-]+)")

_ENGINE_TIMEOUT = 12.0
_TOTAL_BUDGET = 25.0          # 单次 web_search 的总时间预算（秒）
_RETRIES = 1                  # 仅对可恢复错误（TLS/超时/连接）重试
_RETRY_BACKOFF = 0.6
_MAX_PER_DOMAIN = 2           # 同域结果上限，防止整页都是同一个站
_MAX_SNIPPET = 300

# 需要额外请求才能解析的跳转壳：宁缺毋滥，直接丢弃（不做逐条 HEAD 解析）
_OPAQUE_REDIRECTS = (
    "so.com/link?",
    "baidu.com/link?",
    "sogou.com/link?",
    "google.com/url?",
)

# 意图 → 追加限定词：让「求评价」类问题打到论坛/讨论区，而不是品牌官网
_INTENT_RULES = (
    (
        re.compile(r"风评|口碑|评价|差评|吐槽|争议|好用|值得|怎么样|体验|反馈"),
        ("知乎", "v2ex"),
    ),
    (
        re.compile(r"报错|错误|异常|失败|崩溃|闪退|error|traceback|crash", re.IGNORECASE),
        ("issue",),
    ),
    (
        re.compile(r"教程|怎么|如何|配置|安装|上手|入门"),
        ("教程",),
    ),
)
_MAX_VARIANTS = 3


def _clean(text: str) -> str:
    text = _TAG_RE.sub(" ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _proxy_config() -> dict | None:
    """web_search 专用代理：只让搜索引擎请求走代理，其余流量完全不碰。

    设置环境变量 SJTU_WEB_SEARCH_PROXY=http://host:port 后，本工具固定走该
    代理（覆盖 HTTPS_PROXY 全局项）；未设置时保持默认（尊重 HTTP_PROXY /
    HTTPS_PROXY 环境变量，或直连）。适合"服务器配了代理但不想全局开、
    也不担心校园账号走代理"的场景。
    """
    proxy = os.environ.get("SJTU_WEB_SEARCH_PROXY", "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _get(url: str, timeout: float = _ENGINE_TIMEOUT):
    """带一次退避重试的 GET；只重试可恢复错误，HTTP 4xx/5xx 直接抛出。"""
    retryable = (
        requests.exceptions.Timeout,
        requests.exceptions.SSLError,
        requests.exceptions.ProxyError,
        requests.exceptions.ConnectionError,
    )
    for attempt in range(_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers=_HEADERS,
                timeout=timeout,
                proxies=_proxy_config(),
            )
            resp.raise_for_status()
            return resp
        except retryable:
            if attempt >= _RETRIES:
                raise
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError("unreachable")


# ── URL 还原 / 清洗 ──────────────────────────────────────────────────────────


def _decode_bing_redirect(url: str) -> str:
    """从 Bing 的 `ck/a?…&u=a1<base64url>` 还原真实 URL，失败返回空串。"""
    match = _BING_REDIRECT_RE.search(url)
    if not match:
        return ""
    token = match.group(1)
    token += "=" * (-len(token) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(token).decode("utf-8", "replace")
        except Exception:
            continue
        if decoded.startswith(("http://", "https://")):
            return decoded
    return ""


def _real_url(raw_url: str) -> str:
    """把跳转壳还原成真实 URL；还原不了的返回空串（宁可少一条，不给壳链接）。"""
    url = html.unescape(raw_url or "").strip()
    if not url:
        return ""
    lowered = url.lower()
    if "bing.com/ck/a" in lowered or "go.microsoft.com/fwlink" in lowered:
        return _decode_bing_redirect(url)
    if any(marker in lowered for marker in _OPAQUE_REDIRECTS):
        return ""
    return url


_TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|spm|from|wtr|fr|src|ref|referer|share_token|scene|clicktime|"
    r"cota|kuai_so|sign|msclkid|gclid|fbclid)$",
    re.IGNORECASE,
)


def _normalize_url(url: str) -> str:
    """去掉 fragment 与跟踪参数，便于去重与引用。"""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
    kept = [(k, v) for k, v in query if not _TRACKING_PARAMS.match(k)]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), "")
    )


def _is_usable_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _domain(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


# 搜索引擎自家的"站内页"（翻译/图片/短视频/聚合）经常混进结果，不是内容页
_ENGINE_HOST_SUFFIXES = (
    "so.com",
    "360.com",
    "bing.com",
    "baidu.com",
    "sogou.com",
    "google.com",
    "duckduckgo.com",
    "360kan.com",
)


def _is_engine_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ENGINE_HOST_SUFFIXES)


# ── 结果合并 ─────────────────────────────────────────────────────────────────


def _make_item(title: str, url: str, snippet: str) -> dict | None:
    real = _real_url(url)
    title = _clean(title)
    if not title or not _is_usable_url(real):
        return None
    domain = _domain(real)
    if _is_engine_host(domain):
        return None
    return {
        "title": title,
        "url": _normalize_url(real),
        "snippet": _clean(snippet)[:_MAX_SNIPPET],
        "source": domain,
    }


def _merge_results(batches: list[list[dict]], limit: int) -> list[dict]:
    """跨引擎/跨变体合并：按 URL 与「域名+标题」去重，同域最多 _MAX_PER_DOMAIN 条。

    `limit` 是候选池上限（通常大于最终返回条数），排序后再截断——
    否则先到的品牌官网会把后面检索到的有用结果挤掉。
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    per_domain: dict[str, int] = {}
    merged: list[dict] = []
    for batch in batches:
        for item in batch:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            key = f"{item.get('source', '')}|{item.get('title', '')[:40]}"
            if key in seen_titles:
                continue
            domain = item.get("source") or _domain(url)
            if per_domain.get(domain, 0) >= _MAX_PER_DOMAIN:
                continue
            seen_urls.add(url)
            seen_titles.add(key)
            per_domain[domain] = per_domain.get(domain, 0) + 1
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


# ── 相关性打分：结果是否真的打到了「问题焦点」 ────────────────────────────────

# 中文没有分词依赖，用 2-gram 近似；这些高频 gram 不带信息量，剔除
_STOP_GRAMS = {
    "什么", "怎么", "如何", "为什", "的是", "一下", "可以", "这个", "那个",
    "现在", "最近", "以及", "还是", "就是", "有什", "么样", "不会", "没有",
    "么是", "是什", "哪些", "哪个", "是否", "为何", "有没有", "是一", "一个",
}


def _focus_grams(query: str) -> list[str]:
    """把查询拆成焦点 gram：中文 2-gram + 长度 ≥3 的拉丁词（含缩写）。"""
    grams: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        for index in range(len(run) - 1):
            gram = run[index : index + 2]
            if gram not in _STOP_GRAMS and gram not in grams:
                grams.append(gram)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9._+-]*", query):
        lowered = token.lower()
        if len(lowered) >= 3 and lowered not in grams:
            grams.append(lowered)
    return grams


def _hit_count(item: dict, grams: list[str]) -> int:
    haystack = f"{item.get('title', '')} {item.get('snippet', '')} {item.get('url', '')}".lower()
    return sum(1 for gram in grams if gram in haystack)


def _rank(items: list[dict], grams: list[str]) -> list[dict]:
    """按焦点覆盖度排序（稳定排序，同分保持引擎原序）。"""
    if not grams:
        return list(items)
    return sorted(items, key=lambda item: -_hit_count(item, grams))


def _is_thin(items: list[dict], grams: list[str]) -> bool:
    """判断结果是不是「没打到点上」：条数太少，或半数额焦点词完全没被覆盖。

    典型场景：问「Anthropic 风评 / 封号」，Bing 给回一堆 anthropic.com 官网与
    Wikipedia —— 条数很多但「封号」「风评」一个都没出现，此时必须升级来源。
    """
    if len(items) < 3:
        return True
    if not grams:
        return False
    best = max(_hit_count(item, grams) for item in items)
    if best == 0:
        return True
    if len(grams) == 1:
        return False
    return best / len(grams) < 0.5


def _query_variants(query: str) -> list[str]:
    """原始查询 + 缩写查询 + 意图限定词，提高黑话与「求评价」类问题的命中率。"""
    variants = [query]

    acronyms = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", query)
        if token.isupper() and 2 <= len(token) <= 6
    ]
    if acronyms:
        variants.append(" ".join(acronyms))
    base = re.sub(r"\b[A-Z]{2,6}\b", " ", query)
    base = re.sub(r"\s+", " ", base).strip()
    if base and base != query:
        variants.append(base)

    for pattern, hints in _INTENT_RULES:
        if pattern.search(query) and not any(hint in query for hint in hints):
            variants.append(f"{query} {hints[0]}")
            break  # 只取第一条命中的意图，避免限定词越堆越长

    deduped: list[str] = []
    for variant in variants:
        if variant and variant not in deduped:
            deduped.append(variant)
    return deduped[:_MAX_VARIANTS]


# ── 引擎：Bing RSS（主） ─────────────────────────────────────────────────────


def _rss_items(text: str) -> list[str]:
    return re.findall(r"<item>(.*?)</item>", text, re.DOTALL | re.IGNORECASE)


def _rss_field(block: str, tag: str) -> str:
    match = re.search(
        rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.DOTALL | re.IGNORECASE
    )
    return match.group(1) if match else ""


def _search_bing_rss(query: str, max_results: int) -> list[dict]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "format": "rss", "count": max(max_results, 10)}
    )
    resp = _get(url)
    results: list[dict] = []
    for block in _rss_items(resp.text):
        item = _make_item(
            _rss_field(block, "title"),
            _rss_field(block, "link"),
            _rss_field(block, "description"),
        )
        if item:
            results.append(item)
        if len(results) >= max_results:
            break
    return results


# ── 引擎：Bing HTML（备） ────────────────────────────────────────────────────


def _search_bing_html(query: str, max_results: int) -> list[dict]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
    resp = _get(url)
    page = resp.text

    results: list[dict] = []
    # 按 b_algo 切块（不用 </li> 收尾：块内可能有嵌套 li，会截断摘要）
    for block in re.split(r'<li class="b_algo"', page, flags=re.IGNORECASE)[1:]:
        head = re.search(
            r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL | re.IGNORECASE
        )
        if not head:
            continue
        para = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE)
        item = _make_item(
            head.group(2), head.group(1), para.group(1) if para else ""
        )
        if item:
            results.append(item)
        if len(results) >= max_results:
            break
    return results


# ── 引擎：360 搜索（中文兜底） ───────────────────────────────────────────────


class EngineBlocked(RuntimeError):
    """引擎返回反爬/验证页：本轮没有结果，且短期不要再撞它。"""


# 反爬/验证页特征（命中即认为被拦，而不是"没有结果"）
_BLOCK_MARKERS = ("访问异常", "验证码", "访问过于频繁", "captcha", "unusual traffic")

# 被拦截后的冷却：进程内生效，避免每次搜索都白等一轮、也避免把拦截撞得更死
_ENGINE_BLOCKED_UNTIL: dict[str, float] = {}
_BLOCK_COOLDOWN = 600.0


def _looks_blocked(page: str) -> bool:
    head = (page or "")[:4000]
    lowered = head.lower()
    return any(marker in head or marker in lowered for marker in _BLOCK_MARKERS)


def _engine_available(name: str) -> bool:
    return time.monotonic() >= _ENGINE_BLOCKED_UNTIL.get(name, 0.0)


def _mark_blocked(name: str, cooldown: float = _BLOCK_COOLDOWN) -> None:
    _ENGINE_BLOCKED_UNTIL[name] = time.monotonic() + cooldown


def _search_360(query: str, max_results: int) -> list[dict]:
    url = "https://www.so.com/s?" + urllib.parse.urlencode({"q": query})
    resp = _get(url)
    page = resp.text
    if _looks_blocked(page):
        _mark_blocked("so360")
        raise EngineBlocked("so360 返回反爬页")

    results: list[dict] = []
    for block in re.split(r'<li class="res-list', page, flags=re.IGNORECASE)[1:]:
        head = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.DOTALL | re.IGNORECASE)
        if not head:
            continue
        # data-mdurl 是 360 给出的真实地址；href 往往是 so.com/link 壳
        real = re.search(r'data-mdurl="([^"]+)"', block, re.IGNORECASE)
        link = html.unescape(real.group(1)) if real else ""
        if not link:
            anchor = re.search(r'<a[^>]+href="([^"]+)"', head.group(1), re.IGNORECASE)
            link = anchor.group(1) if anchor else ""
        desc = re.search(
            r'<p class="res-desc"[^>]*>(.*?)</p>', block, re.DOTALL | re.IGNORECASE
        )
        item = _make_item(head.group(1), link, desc.group(1) if desc else "")
        if item:
            results.append(item)
        if len(results) >= max_results:
            break
    return results


# ── 编排 ─────────────────────────────────────────────────────────────────────

# 候选池上限：汇齐各来源后按焦点覆盖度排序，再截断成 max_results
_POOL_LIMIT = 24

_ERROR_HINTS = {
    "tls": "TLS/证书错误：多为网络或代理问题；服务器上可设置 SJTU_WEB_SEARCH_PROXY（见 docs/SERVER_DEPLOYMENT.md）",
    "timeout": "请求超时：网络不通或搜索引擎被限流，稍后重试或改用 search_campus / fetch_url",
    "http": "搜索引擎拒绝了请求（限流/反爬），稍后重试或改用其他来源",
    "blocked": "国内搜索引擎临时反爬拦截（已跳过它）：换关键词再搜，或改用 search_campus 看水源社区讨论",
    "official": "官方搜索需要 DeepSeek 官方 API Key（DEEPSEEK_API_KEY）；本轮已回落到免 Key 抓取，也可设 SJTU_WEB_SEARCH_BACKEND=scrapers 明确只用抓取",
    "other": "网络请求失败：检查本机网络；也可改用 search_campus / fetch_url",
}


# ── 搜索即阅读：把对题的前几条正文一并取回 ───────────────────────────────────

_READ_MAX_ITEMS = 3
_READ_PAGE_CHARS = 1500      # 单页上限
_READ_TOTAL_CHARS = 4000     # 整轮上限（避免一次搜索塞爆上下文）
_READ_TIMEOUT = 12.0
_READ_WORKERS = 3
_READ_MIN_SNIPPET = 120      # 摘要短于此，视为需要正文
_MIN_READ_HITS = 2           # 至少命中这么多焦点 gram 才值得读正文


def _read_count(items: list[dict], requested, grams: list[str] | None = None) -> list[dict]:
    """挑出要读正文的条目。

    - 显式传 read_top：按用户/模型要求读前 N 条（无论是否命中焦点）。
    - 自动模式：摘要普遍偏短才读，且**只读命中问题焦点的条目**——否则会把
      预算浪费在「整页品牌官网」上（实测白读 anthropic.com / Wikipedia）。
    """
    if requested is not None:
        try:
            count = max(0, min(int(requested), _READ_MAX_ITEMS))
        except (TypeError, ValueError):
            return []
        return [item for item in items[:count] if item.get("url")]

    rich = sum(
        1 for item in items[:3] if len(item.get("snippet") or "") >= _READ_MIN_SNIPPET
    )
    if rich >= 2:
        return []
    if not grams:
        return [item for item in items[:2] if item.get("url")]
    # 只读「确实打到问题焦点」的条目：品牌官网通常只命中实体名这一个 gram，
    # 阈值取 2 就能把它排除掉，不必浪费一次抓取去读官网首页。
    threshold = min(_MIN_READ_HITS, len(grams))
    return [
        item
        for item in items[:3]
        if item.get("url") and _hit_count(item, grams) >= threshold
    ][:2]


def _read_pages(targets: list[dict], deadline: float) -> tuple[int, int]:
    """并发抓取 targets 的正文，写回 item["content"]。返回（成功数, 失败数）。

    单页失败不影响整轮搜索：失败的条目保持「标题 + 摘要」，模型仍可自行 fetch_url。
    """
    targets = [item for item in targets if item.get("url")]
    if not targets:
        return 0, 0

    budget = max(3.0, min(_READ_TIMEOUT, deadline - time.monotonic()))
    remaining = _READ_TOTAL_CHARS
    done = 0
    failures = 0

    with ThreadPoolExecutor(max_workers=min(_READ_WORKERS, len(targets))) as pool:
        futures = {
            pool.submit(
                fetch_article_text,
                item["url"],
                timeout=_READ_TIMEOUT,
                max_chars=_READ_PAGE_CHARS,
            ): item
            for item in targets
        }
        pending = set(futures)
        try:
            for future in as_completed(futures, timeout=budget):
                pending.discard(future)
                item = futures[future]
                try:
                    data = future.result()
                except Exception:  # noqa: BLE001 — 单页异常只算这一页失败
                    failures += 1
                    continue
                text = (data or {}).get("text") or ""
                if not (data or {}).get("ok") or not text or remaining <= 0:
                    failures += 1
                    continue
                if len(text) > remaining:
                    text = text[:remaining].rstrip() + "\n\n[内容过长，已截断]"
                remaining -= len(text)
                item["content"] = text
                if data.get("title") and len(item.get("title") or "") < 8:
                    item["title"] = data["title"]
                done += 1
        except FuturesTimeout:
            pass  # 超时的页面按失败计
    failures += len(pending)
    return done, failures


def _describe_error(engine: str, exc: Exception) -> tuple[str, str]:
    """把异常归类成（可读错误, hint key），不把原始堆栈丢给模型。"""
    if isinstance(exc, EngineBlocked):
        return f"{engine}: {exc}", "blocked"
    if isinstance(exc, requests.exceptions.SSLError):
        return f"{engine}: TLS 握手失败", "tls"
    if isinstance(exc, requests.exceptions.ProxyError):
        return f"{engine}: 代理连接失败", "tls"
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{engine}: 请求超时", "timeout"
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", "")
        return f"{engine}: HTTP {status}".strip(), "http"
    return f"{engine}: {type(exc).__name__}", "other"


def tool_web_search(
    query: str, max_results: int = 5, read_top: int | None = None
) -> dict:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "query 不能为空"}
    max_results = max(1, min(int(max_results or 5), 8))

    variants = _query_variants(query)
    grams = _focus_grams(query)
    pool: list[dict] = []
    errors: list[str] = []
    used: list[str] = []
    hint_key = ""
    started = time.monotonic()
    deadline = started + _TOTAL_BUDGET

    def attempt(engine: str, searcher, variant: str) -> None:
        nonlocal pool, hint_key
        if time.monotonic() > deadline or len(pool) >= _POOL_LIMIT:
            return
        if not _engine_available(engine):
            return  # 刚被反爬拦截过：冷却期内不再白撞一轮
        try:
            batch = searcher(variant, max_results)
        except Exception as exc:  # noqa: BLE001 — 引擎级隔离，逐级降级
            message, key = _describe_error(engine, exc)
            errors.append(message)
            hint_key = hint_key or key
            return
        if batch:
            pool = _merge_results([pool, batch], _POOL_LIMIT)
            used.append(engine)

    # 第零层：DeepSeek 官方服务端搜索（配了官方 Key 时优先；失败自动回落抓取栈）
    official_count = 0
    if official.enabled():
        try:
            official_batch = official.search(query, max_results)
        except Exception as exc:  # noqa: BLE001 — 官方不可用/失败都回落到抓取
            errors.append(f"deepseek-official: {exc}")
            hint_key = hint_key or "official"
        else:
            if official_batch:
                pool = _merge_results([pool, official_batch], _POOL_LIMIT)
                used.append("deepseek-official")
                official_count = len(official_batch)

    # 官方结果够用时不再打抓取栈（省时间，也少暴露）；不够才升级
    deadline = time.monotonic() + max(8.0, _TOTAL_BUDGET - (time.monotonic() - started))
    if official_count < 3:
        # 第一层：主引擎（Bing RSS，链接与摘要都是真实内容）
        attempt("bing-rss", _search_bing_rss, variants[0])
        # 第二层：同一家引擎的 HTML 端点（RSS 被限流/返回空时）
        if len(pool) < 2:
            attempt("bing-html", _search_bing_html, variants[0])

        # 第三层：结果没打到问题焦点（整页品牌官网/百科）或条数太少 → 中文引擎兜底
        if _is_thin(_rank(pool, grams), grams):
            attempt("so360", _search_360, variants[0])

        # 第四层：仍偏浅 → 换关键词形态（缩写 / 意图限定词）再来一轮
        for variant in variants[1:]:
            if time.monotonic() > deadline or not _is_thin(_rank(pool, grams), grams):
                break
            attempt("bing-rss", _search_bing_rss, variant)
            if _is_thin(_rank(pool, grams), grams):
                attempt("so360", _search_360, variant)

    ranked = _rank(pool, grams)[:max_results]
    if ranked:
        payload: dict = {
            "ok": True,
            "query": query,
            "results": ranked,
            "backend": "+".join(used) or "none",
        }
        if errors:
            # 有来源被限流/失败时如实标注：结果可能偏官方，模型可据此换策略
            payload["degraded"] = errors[-2:]
        # 搜索即阅读：只抓「排在最前、且判定值得读」的那几条
        targets = _read_count(ranked, read_top, grams)
        if targets and time.monotonic() < deadline - 3:
            read_ok, read_failed = _read_pages(targets, deadline)
            if read_ok or read_failed:
                payload["read"] = {"pages": read_ok, "failed": read_failed}
        return payload

    return {
        "ok": False,
        "query": query,
        "error": "联网搜索失败（" + "；".join(errors[-3:] or ["没有返回结果"]) + "）",
        "hint": _ERROR_HINTS.get(hint_key or "other", _ERROR_HINTS["other"]),
        "results": [],
    }
