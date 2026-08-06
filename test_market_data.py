from datetime import date

import pandas as pd
import pytest

from market_data import _default_history_providers, fetch_a_share_history, resolve_a_share


STOCKS = pd.DataFrame(
    {
        "code": ["600519", "000001", "601318", "000002"],
        "name": ["贵州茅台", "平安银行", "中国平安", "万科A"],
    }
)


def test_resolves_stock_by_code():
    assert resolve_a_share("600519", STOCKS) == ("600519", "贵州茅台")


def test_code_does_not_need_stock_list_request():
    assert resolve_a_share("600519") == ("600519", "600519")


def test_resolves_stock_by_exact_name():
    assert resolve_a_share(" 贵州茅台 ", STOCKS) == ("600519", "贵州茅台")


def test_fuzzy_name_returns_suggestions():
    with pytest.raises(ValueError, match="平安银行.*中国平安"):
        resolve_a_share("平安", STOCKS)


def test_unknown_stock_has_clear_error():
    with pytest.raises(ValueError, match="未找到"):
        resolve_a_share("不存在的股票", STOCKS)


def sample_history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-02", periods=4, freq="B"),
            "open": [10.0, 10.2, 10.1, 10.4],
            "high": [10.3, 10.4, 10.5, 10.7],
            "low": [9.9, 10.0, 10.0, 10.2],
            "close": [10.2, 10.1, 10.4, 10.6],
            "volume": [1000, 1200, 900, 1500],
        }
    )


def test_history_falls_back_to_second_provider(tmp_path):
    def failed_provider(*args):
        raise ConnectionError("offline")

    def backup_provider(*args):
        return sample_history()

    result = fetch_a_share_history(
        "000547",
        date(2024, 1, 1),
        date(2024, 1, 10),
        cache_dir=tmp_path,
        providers=[("主数据源", failed_provider), ("备用数据源", backup_provider)],
    )

    assert result.attrs["source"] == "备用数据源"
    assert result.attrs["stale"] is False
    assert len(result) == 4


def test_history_uses_disk_cache_when_all_providers_fail(tmp_path):
    def working_provider(*args):
        return sample_history()

    def failed_provider(*args):
        raise ConnectionError("offline")

    fetch_a_share_history(
        "000547",
        date(2024, 1, 1),
        date(2024, 1, 10),
        cache_dir=tmp_path,
        providers=[("在线源", working_provider)],
    )
    cached = fetch_a_share_history(
        "000547",
        date(2024, 1, 1),
        date(2024, 1, 10),
        cache_dir=tmp_path,
        providers=[("失败源", failed_provider)],
    )

    assert cached.attrs["source"].startswith("本地缓存")
    assert cached.attrs["stale"] is True
    assert len(cached) == 4


def test_etf_history_uses_a_separate_provider_chain(tmp_path):
    def etf_provider(*args):
        return sample_history()

    result = fetch_a_share_history(
        "588000",
        date(2024, 1, 1),
        date(2024, 1, 10),
        instrument_type="etf",
        cache_dir=tmp_path,
        providers=[("ETF 测试源", etf_provider)],
    )

    assert result.attrs["source"] == "ETF 测试源"
    assert [name for name, _ in _default_history_providers("etf")] == [
        "东方财富 ETF",
        "新浪财经 ETF（不复权）",
    ]
