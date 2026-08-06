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


def test_daily_signal_rejects_stale_data_and_outputs_web_link(tmp_path):
    def stale_fetcher(*args, **kwargs):
        prices = sample_prices()
        prices.attrs["source"] = "测试源"
        prices.attrs["stale"] = True
        return prices

    results = run_once(
        database_path=tmp_path / "watchlist.db",
        run_date=date(2026, 8, 6),
        fetcher=stale_fetcher,
    )
    report = format_report(results)

    assert results[0].action == "数据尚未更新"
    assert "https://9s5ky5xuwk4ohrjj5sdk8t.streamlit.app" in report


def test_daily_signal_passes_etf_type_to_data_gateway(tmp_path):
    received = {}

    def fetcher(*args, **kwargs):
        received.update(kwargs)
        prices = sample_prices()
        prices.attrs["source"] = "测试源"
        prices.attrs["stale"] = True
        return prices

    run_once(database_path=tmp_path / "watchlist.db", run_date=date(2026, 8, 6), fetcher=fetcher)
    assert received["instrument_type"] == "etf"
