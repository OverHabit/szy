from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from daily_signal import (
    SignalResult,
    all_official,
    evaluate_entry,
    format_report,
    merge_best,
    parse_deadline,
    run_once,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


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
                "quote_timestamp": pd.Timestamp("2026-08-06 15:01:12"),
            }
        ]
    )
    bar.attrs["source"] = "测试实时行情"
    return bar


def make_result(
    symbol: str, status: str, data_date: date | None, close: float = 10.0
) -> SignalResult:
    return SignalResult(
        symbol=symbol,
        name=symbol,
        strategy_id="ma_crossover",
        action="观望",
        close=close,
        reason="测试",
        status=status,
        data_date=data_date,
        source="测试源",
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
    assert results[0].realtime_at == pd.Timestamp("2026-08-06 15:01:12").to_pydatetime()
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
    assert "实时价格时刻：2026-08-06 15:01:12" in format_report(results)


def test_all_official_needs_the_official_bar_of_the_same_day():
    today = date(2026, 9, 8)
    after_close = datetime(2026, 9, 8, 15, 5, tzinfo=SHANGHAI)

    assert all_official([make_result("600309", "成功", today)], today, now=after_close)
    assert not all_official([make_result("600309", "预估", today)], today, now=after_close)
    assert not all_official(
        [make_result("600309", "成功", date(2026, 9, 7))], today, now=after_close
    )
    assert not all_official([], today, now=after_close)


def test_nothing_counts_as_official_before_the_close():
    today = date(2026, 9, 8)
    before_close = datetime(2026, 9, 8, 10, 30, tzinfo=SHANGHAI)

    assert not all_official(
        [make_result("600309", "成功", today)], today, now=before_close
    )


def test_merge_best_keeps_the_best_result_for_each_symbol():
    today = date(2026, 9, 8)
    first = [
        make_result("600309", "预估", today, 76.7),
        make_result("600879", "成功", today, 14.3),
    ]
    second = [
        make_result("600309", "成功", today, 76.9),
        make_result("600879", "预估", today, 14.2),
    ]

    merged = merge_best(first, second)

    assert [result.status for result in merged] == ["成功", "成功"]
    assert merged[0].close == 76.9
    assert merged[1].close == 14.3


def test_merge_best_keeps_watchlist_order():
    today = date(2026, 9, 8)
    first = [make_result("000547", "成功", today), make_result("600309", "成功", today)]
    second = [make_result("600309", "预估", today), make_result("000547", "预估", today)]

    merged = merge_best(first, second)

    assert [result.symbol for result in merged] == ["000547", "600309"]


def test_report_leads_with_the_symbols_that_changed_signal():
    results = [
        make_result("600309", "成功", date(2026, 9, 8)),
        make_result("000547", "成功", date(2026, 9, 8)),
    ]
    results[0] = replace(results[0], action="策略买入信号")
    results[1] = replace(results[1], action="策略卖出信号")

    report = format_report(results)

    assert report.index("今日信号变化") < report.index("600309（600309）· 双均线趋势")
    assert "600309（600309）策略买入信号" in report
    assert "000547（000547）策略卖出信号" in report


def test_report_says_so_when_nothing_changed():
    results = [make_result("600309", "成功", date(2026, 9, 8))]

    assert "今日信号变化：无买入或卖出，全部为持有或观望。" in format_report(results)


def test_report_summarises_how_many_symbols_are_provisional():
    today = date(2026, 9, 8)
    results = [
        make_result("600309", "预估", today),
        make_result("600879", "成功", today),
    ]

    assert "1/2 个标的当日正式日线未更新" in format_report(results)


def test_parse_deadline_is_bound_to_the_shanghai_session():
    deadline = parse_deadline("18:00", date(2026, 9, 8))

    assert deadline.hour == 18
    assert deadline.tzinfo is not None
    assert deadline.date() == date(2026, 9, 8)
