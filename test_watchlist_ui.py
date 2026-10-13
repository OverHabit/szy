from datetime import date, datetime

from daily_signal import SignalResult
from watchlist_ui import _signal_results_frame


def test_signal_results_frame_shows_runner_details() -> None:
    frame = _signal_results_frame(
        [
            SignalResult(
                symbol="588000",
                name="科创50ETF",
                strategy_id="ma_crossover",
                action="持有",
                close=1.234,
                reason="短期均线高于长期均线。",
                status="预估",
                data_date=date(2026, 8, 6),
                source="东方财富 1 分钟行情",
                realtime_at=datetime(2026, 8, 6, 15, 1, 12),
            )
        ]
    )

    assert frame.to_dict("records") == [
        {
            "代码": "588000",
            "名称": "科创50ETF",
            "策略": "双均线趋势",
            "信号": "持有",
            "状态": "预估",
            "数据日期": "2026-08-06",
            "实时价格时刻": "2026-08-06 15:01:12",
            "参考收盘价": 1.234,
            "数据源": "东方财富 1 分钟行情",
            "说明": "短期均线高于长期均线。",
        }
    ]


def test_delete_button_removes_default_and_shows_success(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import watchlist_store

    monkeypatch.setattr(watchlist_store, "DATABASE_PATH", tmp_path / "watchlist.db")
    app = AppTest.from_string(
        "from watchlist_ui import render_watchlist_page\nrender_watchlist_page()"
    ).run()
    assert not app.exception
    target = next(expander for expander in app.expander if "001283" in expander.label)
    next(button for button in target.button if button.label == "删除此项").click().run()
    assert not app.exception
    assert any("已删除 豪鹏科技（001283）" in message.value for message in app.success)
    assert all("001283" not in expander.label for expander in app.expander)
    app.run()
    assert not app.exception
    assert all("001283" not in expander.label for expander in app.expander)
    assert len(watchlist_store.list_watchlist()) == len(watchlist_store.DEFAULT_WATCHLIST) - 1


def test_delete_failure_is_visible_and_keeps_entry(tmp_path, monkeypatch):
    import sqlite3
    from streamlit.testing.v1 import AppTest
    import watchlist_store
    import watchlist_ui

    monkeypatch.setattr(watchlist_store, "DATABASE_PATH", tmp_path / "watchlist.db")

    def fail_delete(entry_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(watchlist_ui, "delete_watchlist_entry", fail_delete)
    app = AppTest.from_string(
        "from watchlist_ui import render_watchlist_page\nrender_watchlist_page()"
    ).run()
    target = next(expander for expander in app.expander if "001283" in expander.label)
    next(button for button in target.button if button.label == "删除此项").click().run()
    assert not app.exception
    assert any("无法删除：database is locked" in message.value for message in app.error)
    assert len(watchlist_store.list_watchlist()) == len(watchlist_store.DEFAULT_WATCHLIST)
