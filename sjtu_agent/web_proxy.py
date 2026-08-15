"""
sjtu_agent/web_proxy.py — 为 `sjtu-agent web` 生成 HTTPS 反向代理配置

本地 Web UI 默认只监听 127.0.0.1；如需远程访问，推荐：
    sjtu-agent web --host 0.0.0.0 --port 7860 --no-browser

然后由 Nginx / Caddy 提供 HTTPS 反代。这里只生成可直接使用的配置片段。
"""

from __future__ import annotations

import re
from pathlib import Path

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)


def validate_domain(domain: str) -> str:
    value = domain.strip().lower().rstrip(".")
    if not value or not _DOMAIN_RE.match(value):
        raise ValueError(
            f"无效的域名：{domain!r}。HTTPS 反代需要一个真实域名（如 sjtu-agent.example.com）。"
        )
    return value


def validate_port(port: int, label: str = "port") -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是 1-65535 的端口号") from exc
    if not 1 <= value <= 65535:
        raise ValueError(f"{label} 必须是 1-65535 的端口号")
    return value


def _nginx_config(domain: str, backend_port: int) -> str:
    return f"""\
# SJTU Agent Web UI — Nginx HTTPS 反向代理
# 放置到 /etc/nginx/conf.d/sjtu-agent.conf 后执行：
#   nginx -t && systemctl reload nginx
#
# 首次申请证书：
#   apt install -y nginx certbot python3-certbot-nginx
#   certbot --nginx -d {domain}

server {{
    listen 443 ssl http2;
    server_name {domain};

    ssl_certificate     /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location / {{
        proxy_pass http://127.0.0.1:{backend_port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Web UI 聊天使用 SSE 长连接，必须关闭缓冲并放宽超时。
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_connect_timeout 10s;
    }}
}}

server {{
    listen 80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}
"""


def _caddy_config(domain: str, backend_port: int) -> str:
    return f"""\
# SJTU Agent Web UI — Caddy 自动 HTTPS 反向代理
# 放入 Caddyfile（或 /etc/caddy/Caddyfile 对应站点块）后执行：
#   caddy validate --config Caddyfile
#   systemctl reload caddy

{domain} {{
    reverse_proxy 127.0.0.1:{backend_port} {{
        # SSE 长连接
        flush_interval -1
    }}
}}
"""


def generate_proxy_config(
    kind: str,
    domain: str,
    backend_port: int = 7860,
) -> str:
    """生成 Nginx 或 Caddy 的 HTTPS 反向代理配置。"""
    kind_value = (kind or "nginx").strip().lower()
    domain_value = validate_domain(domain)
    backend_port_value = validate_port(backend_port, "backend port")

    if kind_value == "nginx":
        return _nginx_config(domain_value, backend_port_value)
    if kind_value == "caddy":
        return _caddy_config(domain_value, backend_port_value)
    raise ValueError(f"不支持的反代类型：{kind!r}（可选 nginx / caddy）")


def write_proxy_config(
    destination: Path,
    kind: str,
    domain: str,
    backend_port: int = 7860,
    force: bool = False,
) -> Path:
    destination = Path(destination)
    if destination.exists() and not force:
        raise FileExistsError(f"目标文件已存在（--force 可覆盖）：{destination}")
    content = generate_proxy_config(kind, domain, backend_port)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return destination
