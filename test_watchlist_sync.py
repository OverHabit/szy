"""跨端同步（watchlist_sync）的离线测试：用内存假远端替换 GitHub API。

口径：网页端为准 —— 远端 revision 更新时本机让位，本机只有在基于最新远端时才允许推送。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import watchlist_sync
from watchlist_store import (
    delete_watchlist_entry,
    get_setting,
    list_watchlist,
    replace_watchlist,
    save_watchlist_entry,
)

CONFIG = {"token": "fake", "repo": "owner/repo", "branch": "state", "path": "watchlist.json"}


class FakeRemote:
    """最小可用的 GitHub Contents API 替身：只实现 GET / PUT 一个文件。"""

    def __init__(self) -> None:
        self.state: dict | None = None
        self.sha: str | None = None
        self.down = False

    def __call__(self, cfg, method, url, payload=None):
        if self.down:
            raise watchlist_sync.SyncError("无法访问 GitHub：URLError")
        if method == "GET":
            if self.state is None:
                return 404, "Not Found"
            blob = base64.b64encode(json.dumps(self.state, ensure_ascii=False).encode()).decode()
            return 200, {"content": blob, "sha": self.sha}
        if method == "PUT":
            assert payload["branch"] == CONFIG["branch"]
            self.state = json.loads(base64.b64decode(payload["content"]).decode())
            self.sha = f"sha-{self.state['revision']}"
            return 201, {}
        raise AssertionError(f"未预期的请求 {method} {url}")


@pytest.fixture()
def remote(monkeypatch):
    fake = FakeRemote()
    monkeypatch.setattr(watchlist_sync, "_request", fake)
    monkeypatch.setattr(watchlist_sync, "config", lambda: dict(CONFIG))
    return fake


def _add(symbol: str, database_path: Path) -> None:
    save_watchlist_entry(
        {"symbol": symbol, "name": "", "instrument_type": "stock",
         "strategy_id": "ma_crossover", "enabled": True},
        database_path,
    )


def test_remote_missing_initialises_from_this_machine(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    result = watchlist_sync.pull(local)

    assert result["status"] == "pushed"
    assert result["count"] == 11
    assert get_setting("sync_revision", "0", local) == str(result["revision"])


def test_push_then_pull_reproduces_the_same_watchlist(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    entries = list_watchlist(local)
    delete_watchlist_entry(entries[0]["id"], local)

    result = watchlist_sync.push(local)
    assert result["status"] == "pushed"
    assert result["count"] == len(entries) - 1

    fresh = tmp_path / "fresh.db"
    assert len(list_watchlist(fresh)) == 11  # 新库先落默认清单
    pulled = watchlist_sync.pull(fresh)

    assert pulled["status"] == "pulled"
    symbols = [entry["symbol"] for entry in list_watchlist(fresh)]
    assert entries[0]["symbol"] not in symbols
    assert len(symbols) == len(entries) - 1


def test_remote_change_replaces_local_once(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    watchlist_sync.push(local)
    remote.state["entries"] = [e for e in remote.state["entries"] if e["symbol"] != "000547"]
    remote.state["revision"] += 1

    first = watchlist_sync.pull(local)
    assert first["status"] == "pulled"
    assert all(entry["symbol"] != "000547" for entry in list_watchlist(local))

    second = watchlist_sync.pull(local)
    assert second["status"] == "up_to_date"


def test_local_edit_is_pushed_when_remote_has_not_moved(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    watchlist_sync.push(local)  # rev 1

    _add("600519", local)
    watchlist_sync.mark_pending(local)

    result = watchlist_sync.pull(local)
    assert result["status"] == "pushed"
    assert result["revision"] == 2
    assert any(entry["symbol"] == "600519" for entry in remote.state["entries"])


def test_remote_wins_over_unpushed_local_change(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    watchlist_sync.push(local)  # rev 1

    _add("600519", local)                       # 本机改了但还没推
    watchlist_sync.mark_pending(local)
    remote.state = {"version": 1, "revision": 5, "updated_at": "2026-10-21T08:00:00+08:00",
                    "entries": [{"symbol": "600000", "name": "浦发银行", "instrument_type": "stock",
                                 "strategy_id": "ma_crossover", "enabled": 1}]}   # 网页端也改了

    result = watchlist_sync.pull(local)

    assert result["status"] == "pulled"
    assert result["discarded_local"] is True
    assert [entry["symbol"] for entry in list_watchlist(local)] == ["600000"]
    assert get_setting("sync_pending", "0", local) == "0"


def test_push_is_refused_when_remote_is_ahead(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    watchlist_sync.push(local)  # rev 1
    remote.state["revision"] = 7

    result = watchlist_sync.push(local)

    assert result["status"] == "superseded"
    assert result["revision"] == 7


def test_force_push_overrides_remote(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    watchlist_sync.push(local)
    remote.state["revision"] = 9

    result = watchlist_sync.push(local, force=True)
    assert result["status"] == "pushed"
    assert result["revision"] == 10


def test_corrupt_remote_entry_is_skipped_instead_of_blocking_sync(remote, tmp_path: Path):
    local = tmp_path / "local.db"
    written = replace_watchlist(
        [
            {"symbol": "600519", "name": "贵州茅台", "instrument_type": "stock",
             "strategy_id": "ma_crossover"},
            {"symbol": "abc", "strategy_id": "ma_crossover"},
        ],
        local,
    )
    assert written == 1
    assert [entry["symbol"] for entry in list_watchlist(local)] == ["600519"]


def test_network_failure_degrades_without_raising(remote, monkeypatch, tmp_path: Path):
    monkeypatch.delenv("WATCHLIST_SYNC_DISABLED", raising=False)  # conftest 默认关掉同步
    remote.down = True
    result = watchlist_sync.ensure_synced(tmp_path / "local.db")
    assert result["status"] == "error"
    assert "GitHub" in result["message"]


def test_sync_is_disabled_without_a_token(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(watchlist_sync, "_token", lambda: None)
    assert watchlist_sync.sync_available() is False
    result = watchlist_sync.ensure_synced(tmp_path / "local.db")
    assert result["status"] == "disabled"
    assert len(list_watchlist(tmp_path / "local.db")) == 11  # 仍按本机默认清单工作
