from datetime import date

import numpy as np
import pandas as pd

from daily_signal import evaluate_entry, format_report, run_once


def sample_prices(rows: int = 80) -> pd.DataFrame:
    close = np.concatenate([np.linspace(10, 9, rows // 2), np.linspace(9, 15, rows // 2)])
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2026-04-17", periods=rows),
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 100_000,
        }
    )


def sample_realtime_bar() -> pd.DataFrame:
    bar = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-08-06"),
                "open": 15.0,
                "high": 15.2,
                "low": 14.9,
                "close": 15.1,
                "volume": 100_000,
                "previous_close": 15.0,
            }
        ]
    )
    bar.attrs["source"] = "测试实时行情"
    return bar


def test_ma_signal_reports_current_holding_state():
    result = evaluate_entry(
        {
            "symbol": "588000",
            "name": "科创50ETF",
            "strategy_id": "ma_crossover",
            "short_window": 5,
            "long_window": 20,
            "fee_bps": 3.0,
        },
        sample_prices(),
    )

    assert result.action == "持有"
    assert "短期均线" in result.reason


def test_daily_signal_uses_realtime_quote_when_daily_data_is_stale(tmp_path):
    def stale_fetcher(*args, **kwargs):
        prices = sample_prices()
        prices.attrs["source"] = "测试源"
        prices.attrs["stale"] = True
        return prices

    results = run_once(
        database_path=tmp_path / "watchlist.db",
        run_date=date(2026, 8, 6),
        fetcher=stale_fetcher,
        realtime_fetcher=lambda *_: sample_realtime_bar(),
    )
    report = format_report(results)

    assert results[0].status == "预估"
    assert results[0].data_date == date(2026, 8, 6)
    assert results[0].source == "测试实时行情（按昨日复权系数换算）"
    assert results[0].action in {"持有", "观望", "策略买入信号", "策略卖出信号"}
    assert "https://9s5ky5xuwk4ohrjj5sdk8t.streamlit.app" in report


def test_daily_signal_passes_etf_type_to_data_gateway(tmp_path):
    received = set()

    def fetcher(*args, **kwargs):
        received.add(kwargs["instrument_type"])
        prices = sample_prices()
        prices.attrs["source"] = "测试源"
        prices.attrs["stale"] = True
        return prices

    run_once(
        database_path=tmp_path / "watchlist.db",
        run_date=date(2026, 8, 6),
        fetcher=fetcher,
        realtime_fetcher=lambda *_: sample_realtime_bar(),
    )
    assert received == {"stock", "etf"}


def test_daily_signal_uses_realtime_quote_when_official_daily_bar_is_missing(tmp_path):
    def daily_fetcher(*args, **kwargs):
        prices = sample_prices().iloc[:-1].copy()
        prices.attrs["source"] = "测试日线"
        prices.attrs["stale"] = False
        return prices

    results = run_once(
        database_path=tmp_path / "watchlist.db",
        run_date=date(2026, 8, 6),
        fetcher=daily_fetcher,
        realtime_fetcher=lambda *_: sample_realtime_bar(),
    )

    assert results[0].status == "预估"
    assert results[0].data_date == date(2026, 8, 6)
    assert results[0].source == "测试实时行情（按昨日复权系数换算）"
    assert "实时收盘行情预估" in format_report(results)
