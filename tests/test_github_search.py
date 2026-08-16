from __future__ import annotations

from sjtu_agent.agent.tools._github_search import tool_github_repo_search


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_github_repo_search_parses_results(monkeypatch):
    payload = {
        "total_count": 1,
        "items": [
            {
                "full_name": "kuan-er/sjtu-agent",
                "name": "sjtu-agent",
                "owner": {"login": "kuan-er"},
                "html_url": "https://github.com/kuan-er/sjtu-agent",
                "description": "SJTU campus agent",
                "stargazers_count": 81,
                "language": "Python",
                "updated_at": "2026-08-15T00:00:00Z",
            }
        ],
    }
    monkeypatch.setattr(
        "sjtu_agent.agent.tools._github_search.requests.get",
        lambda *a, **k: FakeResponse(payload),
    )
    result = tool_github_repo_search("sjtu-agent")
    assert result["ok"] is True
    assert result["results"][0]["full_name"] == "kuan-er/sjtu-agent"
    assert result["results"][0]["owner"] == "kuan-er"
    assert result["results"][0]["url"] == "https://github.com/kuan-er/sjtu-agent"


def test_github_repo_search_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "sjtu_agent.agent.tools._github_search.requests.get",
        lambda *a, **k: FakeResponse(status_code=429),
    )
    result = tool_github_repo_search("sjtu-agent")
    assert result["ok"] is False
    assert "429" in result["error"]


def test_github_repo_search_empty_query():
    result = tool_github_repo_search(" ")
    assert result["ok"] is False
    assert "query" in result["error"]
