from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume"}


@dataclass(frozen=True)
class BacktestMetrics:
    total_return: float
    benchmark_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float
    trade_count: int
    win_rate: float


def run_ma_crossover(
    price_data: pd.DataFrame,
    short_window: int = 10,
    long_window: int = 30,
    fee_bps: float = 3.0,
) -> tuple[pd.DataFrame, BacktestMetrics, pd.DataFrame]:
    """Run a long-only moving-average crossover backtest.

    Signals observed at today's close are executed for the next trading day.
    Trading fees are charged once whenever the position changes.
    """
    missing = REQUIRED_COLUMNS.difference(price_data.columns)
    if missing:
        raise ValueError(f"行情数据缺少字段: {', '.join(sorted(missing))}")
    if short_window >= long_window:
        raise ValueError("短期均线必须小于长期均线")
    if short_window < 2:
        raise ValueError("短期均线至少为 2 日")
    if len(price_data) <= long_window:
        raise ValueError(f"有效行情不足，至少需要 {long_window + 1} 个交易日")

    result = price_data.copy().sort_values("date").reset_index(drop=True)
    result["ma_short"] = result["close"].rolling(short_window).mean()
    result["ma_long"] = result["close"].rolling(long_window).mean()
    result["signal"] = (result["ma_short"] > result["ma_long"]).astype(int)
    result["position"] = result["signal"].shift(1).fillna(0).astype(int)
    result["asset_return"] = result["close"].pct_change().fillna(0.0)
    result["turnover"] = result["position"].diff().abs().fillna(0.0)
    result["strategy_return"] = (
        result["position"] * result["asset_return"]
        - result["turnover"] * fee_bps / 10_000
    )
    result["strategy_equity"] = (1 + result["strategy_return"]).cumprod()
    result["benchmark_equity"] = (1 + result["asset_return"]).cumprod()
    result["equity_peak"] = result["strategy_equity"].cummax()
    result["drawdown"] = result["strategy_equity"] / result["equity_peak"] - 1
    result["trade"] = result["position"].diff().fillna(0).astype(int)

    trades = _build_trade_log(result, fee_bps)
    metrics = _calculate_metrics(result, trades)
    return result, metrics, trades


def _build_trade_log(result: pd.DataFrame, fee_bps: float) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    entry_date = None
    entry_price = None

    for row in result.itertuples(index=False):
        if row.trade == 1:
            entry_date = row.date
            entry_price = float(row.close)
        elif row.trade == -1 and entry_date is not None and entry_price is not None:
            exit_price = float(row.close)
            net_return = exit_price / entry_price - 1 - 2 * fee_bps / 10_000
            records.append(
                {
                    "买入日期": entry_date,
                    "卖出日期": row.date,
                    "买入价": entry_price,
                    "卖出价": exit_price,
                    "收益率": net_return,
                    "状态": "已平仓",
                }
            )
            entry_date = None
            entry_price = None

    if entry_date is not None and entry_price is not None:
        last = result.iloc[-1]
        net_return = float(last["close"]) / entry_price - 1 - fee_bps / 10_000
        records.append(
            {
                "买入日期": entry_date,
                "卖出日期": pd.NaT,
                "买入价": entry_price,
                "卖出价": float(last["close"]),
                "收益率": net_return,
                "状态": "持仓中",
            }
        )

    columns = ["买入日期", "卖出日期", "买入价", "卖出价", "收益率", "状态"]
    return pd.DataFrame(records, columns=columns)


def _calculate_metrics(
    result: pd.DataFrame, trades: pd.DataFrame
) -> BacktestMetrics:
    total_return = float(result["strategy_equity"].iloc[-1] - 1)
    benchmark_return = float(result["benchmark_equity"].iloc[-1] - 1)
    years = max(len(result) / 252, 1 / 252)
    annual_return = float((1 + total_return) ** (1 / years) - 1)
    max_drawdown = float(result["drawdown"].min())

    daily_std = float(result["strategy_return"].std(ddof=0))
    sharpe = (
        float(np.sqrt(252) * result["strategy_return"].mean() / daily_std)
        if daily_std > 0
        else 0.0
    )
    closed = trades[trades["状态"] == "已平仓"]
    win_rate = float((closed["收益率"] > 0).mean()) if not closed.empty else 0.0

    return BacktestMetrics(
        total_return=total_return,
        benchmark_return=benchmark_return,
        annual_return=annual_return,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        trade_count=len(trades),
        win_rate=win_rate,
    )
