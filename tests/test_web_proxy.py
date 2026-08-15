from __future__ import annotations

import pytest

from sjtu_agent.web_proxy import generate_proxy_config, validate_domain, write_proxy_config


def test_validate_domain():
    assert validate_domain("sjtu-agent.example.com") == "sjtu-agent.example.com"
    assert validate_domain("SJTU-Agent.Example.COM.") == "sjtu-agent.example.com"
    with pytest.raises(ValueError):
        validate_domain("https://sjtu-agent.example.com")
    with pytest.raises(ValueError):
        validate_domain("not a domain")


def test_generate_nginx_config():
    config = generate_proxy_config("nginx", "sjtu-agent.example.com", 7860)
    assert "server_name sjtu-agent.example.com;" in config
    assert "proxy_pass http://127.0.0.1:7860;" in config
    assert "proxy_buffering off;" in config
    assert "listen 443 ssl" in config
    assert "return 301 https://$host$request_uri;" in config


def test_generate_caddy_config():
    config = generate_proxy_config("caddy", "sjtu-agent.example.com", 8080)
    assert "sjtu-agent.example.com {" in config
    assert "reverse_proxy 127.0.0.1:8080" in config
    assert "flush_interval -1" in config


def test_unknown_proxy_type():
    with pytest.raises(ValueError, match="不支持"):
        generate_proxy_config("apache", "sjtu-agent.example.com")


def test_write_proxy_config(tmp_path):
    target = tmp_path / "nginx.conf"
    written = write_proxy_config(target, "nginx", "sjtu-agent.example.com")
    assert written == target
    assert "proxy_pass" in target.read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_proxy_config(target, "nginx", "sjtu-agent.example.com")

    write_proxy_config(target, "nginx", "sjtu-agent.example.com", force=True)
