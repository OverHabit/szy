from datetime import date

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
            "参考收盘价": 1.234,
            "数据源": "东方财富 1 分钟行情",
            "说明": "短期均线高于长期均线。",
        }
    ]
