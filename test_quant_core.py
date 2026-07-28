import numpy as np
import pandas as pd

from quant_core import run_ma_crossover


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
