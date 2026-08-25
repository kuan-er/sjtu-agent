"""Protocol compatibility tests for the WeChat iLink client."""

import json

import pytest

from scripts import wechat_bot
from scripts.wechat_bot import ILinkClient


def test_ilink_headers_identify_the_bot_client():
    headers = ILinkClient("test-token")._headers()

    assert headers["iLink-App-Id"] == "bot"
    assert headers["iLink-App-ClientVersion"] == "65539"


@pytest.mark.parametrize(
    "payload",
    [
        {"ret": -14, "errmsg": "session timeout"},
        {"ret": 0, "errcode": -14, "errmsg": "session timeout"},
    ],
    ids=["ret", "errcode"],
)
def test_get_updates_rejects_each_ilink_error_code(monkeypatch, payload):
    class FakeResponse:
        status_code = 200
        text = json.dumps(payload)

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(wechat_bot.httpx, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError, match="session timeout"):
        ILinkClient("expired-token").get_updates()


def test_get_updates_rejects_http_error_response(monkeypatch):
    class FakeResponse:
        status_code = 502
        text = "{}"

        def raise_for_status(self):
            raise RuntimeError("HTTP 502")

    monkeypatch.setattr(wechat_bot.httpx, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError, match="HTTP 502"):
        ILinkClient("test-token").get_updates()
