import numpy as np
import pandas as pd

from quant_core import run_ma_crossover, run_volume_breakout


def sample_prices(rows: int = 80) -> pd.DataFrame:
    close = np.concatenate(
        [np.linspace(100, 90, rows // 2), np.linspace(90, 125, rows // 2)]
    )
    dates = pd.bdate_range("2024-01-02", periods=rows)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 100_000,
        }
    )


def test_ma_crossover_generates_delayed_position_and_metrics():
    result, metrics, trades = run_ma_crossover(
        sample_prices(), short_window=5, long_window=15, fee_bps=3
    )

    first_signal = result.index[result["signal"].diff() == 1][0]
    assert result.loc[first_signal, "position"] == 0
    assert result.loc[first_signal + 1, "position"] == 1
    assert metrics.trade_count == 1
    assert len(trades) == 1
    assert result["strategy_equity"].iloc[-1] > 1


def test_rejects_invalid_windows():
    try:
        run_ma_crossover(sample_prices(), short_window=20, long_window=10)
    except ValueError as exc:
        assert "短期均线" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_volume_breakout_uses_prior_high_and_prior_average_volume():
    prices = sample_prices(rows=30)
    prices["close"] = [10.0] * 20 + [11.0, 11.5, 12.0] + [9.0] * 7
    prices["open"] = prices["close"]
    prices["high"] = prices["close"] + 0.5
    prices["low"] = prices["close"] - 0.5
    prices["volume"] = [100] * 20 + [200] + [100] * 9

    result, _, _ = run_volume_breakout(
        prices,
        breakout_window=5,
        volume_window=5,
        volume_multiple=1.5,
        exit_window=3,
    )

    breakout_index = 20
    assert result.loc[breakout_index, "entry_signal"]
    assert result.loc[breakout_index, "position"] == 0
    assert result.loc[breakout_index + 1, "position"] == 1
    assert result.loc[breakout_index, "volume_ratio"] == 2.0
    assert result.loc[23, "signal"] == 0
    assert result.loc[24, "position"] == 0


def test_volume_breakout_rejects_nonpositive_volume_multiple():
    try:
        run_volume_breakout(sample_prices(), volume_multiple=0)
    except ValueError as exc:
        assert "放量倍数" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
