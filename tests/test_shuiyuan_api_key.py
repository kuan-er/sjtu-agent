from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from sjtu_agent.agent.tools import _core as core


class _FakeResponse:
    status_code = 200


def test_user_api_key_start_and_submit(monkeypatch, tmp_path):
    pending_path = tmp_path / "pending.json"
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core, "SHUIYUAN_API_PENDING_PATH", pending_path)
    monkeypatch.setattr(core, "CONFIG_PATH", config_path)

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    start = core.tool_start_shuiyuan_api_key()
    assert start.get("success") is True
    assert opened

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    private_key = serialization.load_pem_private_key(
        pending["private_key_pem"].encode("ascii"),
        password=None,
    )
    response_payload = {"key": "test-user-api-key", "nonce": pending["nonce"]}
    encrypted = private_key.public_key().encrypt(
        json.dumps(response_payload).encode("utf-8"),
        padding.PKCS1v15(),
    )

    monkeypatch.setattr(core.requests, "get", lambda *a, **kw: _FakeResponse())

    result = core.tool_submit_shuiyuan_api_key(base64.b64encode(encrypted).decode("ascii"))
    assert result.get("success") is True
    assert result.get("api_key_saved") is True
    assert not pending_path.exists()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["shuiyuan_user_api_key"] == "test-user-api-key"
    assert saved["shuiyuan_user_api_client_id"] == pending["client_id"]
