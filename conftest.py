"""测试环境守卫：默认禁止任何测试打到真实 GitHub。

`watchlist_sync` 的写操作会直接改动跨端同步状态，一旦测试误用真实凭据，
轻则污染远端清单，重则把网页端的修改覆盖掉。这里把网络层默认换成"一用就报错"，
需要假远端的测试再自行 monkeypatch 覆盖。
"""

from __future__ import annotations

import pytest

import watchlist_sync


@pytest.fixture(autouse=True)
def _forbid_real_github(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("测试不允许访问真实 GitHub API")

    monkeypatch.setattr(watchlist_sync, "_request", _blocked)
    # AppTest 会在子进程里跑 app.py，环境变量能一并传下去，让那条链路也关掉同步
    monkeypatch.setenv("WATCHLIST_SYNC_DISABLED", "1")
