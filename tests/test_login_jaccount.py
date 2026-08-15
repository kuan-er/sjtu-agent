from __future__ import annotations

import pytest

import login


class _FakePageNoForm:
    url = "https://jaccount.sjtu.edu.cn/"

    def evaluate(self, *args, **kwargs):
        return None

    def wait_for_timeout(self, *args, **kwargs):
        return None

    def wait_for_selector(self, *args, **kwargs):
        raise TimeoutError("no login form")


def test_fill_jaccount_reports_missing_form_clearly():
    with pytest.raises(RuntimeError, match="登录框未加载"):
        login._fill_jaccount(_FakePageNoForm(), "user", "password")
