"""Create an email-ready daily report from the locally managed watchlist.

This script has no email password and never places an order. Hermes runs it as
a script-only task, then delivers its standard output through the already
configured email channel.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from market_data import fetch_a_share_history, fetch_provisional_daily_bar
from quant_core import run_ma_crossover, run_volume_breakout
from watchlist_store import DATABASE_PATH, list_watchlist


APP_URL = "https://9s5ky5xuwk4ohrjj5sdk8t.streamlit.app"
STRATEGY_NAMES = {"ma_crossover": "双均线趋势", "volume_breakout": "放量突破"}
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class SignalResult:
    symbol: str
    name: str
    strategy_id: str
    action: str
    close: float | None
    reason: str
    status: str
    data_date: date | None
    source: str | None


def evaluate_entry(entry: dict[str, Any], prices: pd.DataFrame) -> SignalResult:
    """Evaluate today's and yesterday's strategy state without future data."""
    if entry["strategy_id"] == "ma_crossover":
        result, _, _ = run_ma_crossover(
            prices,
            short_window=int(entry["short_window"]),
            long_window=int(entry["long_window"]),
            fee_bps=float(entry["fee_bps"]),
        )
        current_signal = int(result["signal"].iloc[-1])
        reason = (
            f"短期均线 {result['ma_short'].iloc[-1]:.2f} "
            f"{'高于' if current_signal else '不高于'} "
            f"长期均线 {result['ma_long'].iloc[-1]:.2f}。"
        )
    elif entry["strategy_id"] == "volume_breakout":
        result, _, _ = run_volume_breakout(
            prices,
            breakout_window=int(entry["breakout_window"]),
            volume_window=int(entry["volume_window"]),
            volume_multiple=float(entry["volume_multiple"]),
            exit_window=int(entry["exit_window"]),
            fee_bps=float(entry["fee_bps"]),
        )
        current_signal = int(result["signal"].iloc[-1])
        if bool(result["entry_signal"].iloc[-1]):
            reason = (
                f"收盘突破 {int(entry['breakout_window'])} 日高点，"
                f"量比 {result['volume_ratio'].iloc[-1]:.2f}。"
            )
        elif bool(result["exit_signal"].iloc[-1]):
            reason = (
                f"收盘跌破 {int(entry['exit_window'])} 日退出均线 "
                f"{result['exit_ma'].iloc[-1]:.2f}。"
            )
        else:
            reason = "未出现新的突破或退出条件。"
    else:
        raise ValueError("不支持的策略")

    previous_signal = int(result["signal"].iloc[-2])
    action = {
        (0, 1): "策略买入信号",
        (1, 0): "策略卖出信号",
        (1, 1): "持有",
        (0, 0): "观望",
    }[(previous_signal, current_signal)]
    return SignalResult(
        symbol=str(entry.get("symbol", "")),
        name=str(entry.get("name", "")),
        strategy_id=str(entry["strategy_id"]),
        action=action,
        close=float(result["close"].iloc[-1]),
        reason=reason,
        status="成功",
        data_date=pd.Timestamp(result["date"].iloc[-1]).date(),
        source=None,
    )


def run_once(
    *,
    database_path=DATABASE_PATH,
    run_date: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_a_share_history,
    minute_fetcher: Callable[[str, date, str], pd.DataFrame] = fetch_provisional_daily_bar,
) -> list[SignalResult]:
    """Fetch and evaluate each enabled configuration once."""
    today = run_date or datetime.now(SHANGHAI).date()
    start = today - timedelta(days=550)
    results: list[SignalResult] = []
    for entry in list_watchlist(database_path, enabled_only=True):
        latest_date: date | None = None
        source: str | None = None
        try:
            prices = fetcher(
                entry["symbol"],
                start,
                today,
                entry["adjust"],
                instrument_type=entry["instrument_type"],
            )
            latest_date = pd.Timestamp(prices["date"].iloc[-1]).date()
            source = str(prices.attrs.get("source", "未知数据源"))
            if not bool(prices.attrs.get("stale", False)) and latest_date == today:
                evaluated = evaluate_entry(entry, prices)
                results.append(replace(evaluated, source=source, data_date=latest_date))
                continue

            provisional_prices, provisional_source = _with_provisional_daily_bar(
                entry, prices, today, start, fetcher, minute_fetcher
            )
            evaluated = evaluate_entry(entry, provisional_prices)
            results.append(
                replace(
                    evaluated,
                    status="预估",
                    data_date=today,
                    source=provisional_source,
                    reason=f"{evaluated.reason} 该结果由 15:00 分钟行情聚合，待正式日线确认。",
                )
            )
        except Exception as exc:
            results.append(
                SignalResult(
                    symbol=str(entry["symbol"]),
                    name=str(entry["name"]),
                    strategy_id=str(entry["strategy_id"]),
                    action="数据尚未更新",
                    close=None,
                    reason=f"正式日线尚未可用，分钟线临时日线也无法确认：{exc}",
                    status="等待数据",
                    data_date=latest_date,
                    source=source,
                )
            )
    return results


def _with_provisional_daily_bar(
    entry: dict[str, Any],
    prices: pd.DataFrame,
    today: date,
    start: date,
    fetcher: Callable[..., pd.DataFrame],
    minute_fetcher: Callable[[str, date, str], pd.DataFrame],
) -> tuple[pd.DataFrame, str]:
    base = prices.loc[pd.to_datetime(prices["date"]).dt.date < today].copy()
    if base.empty:
        raise ValueError("缺少昨日及以前的日线，无法计算临时日线策略")
    provisional = minute_fetcher(entry["symbol"], today, entry["instrument_type"])
    if provisional.empty:
        raise ValueError("分钟行情未能生成临时日线")
    source = str(provisional.attrs.get("source", "分钟行情"))

    if entry["adjust"]:
        raw = fetcher(
            entry["symbol"],
            start,
            today,
            "",
            instrument_type=entry["instrument_type"],
        )
        previous_date = pd.Timestamp(base["date"].iloc[-1]).date()
        raw_previous = raw.loc[pd.to_datetime(raw["date"]).dt.date == previous_date]
        if raw_previous.empty or float(raw_previous["close"].iloc[-1]) <= 0:
            raise ValueError("无法取得与复权日线对应的昨日原始价格")
        factor = float(base["close"].iloc[-1]) / float(raw_previous["close"].iloc[-1])
        provisional = provisional.copy()
        for column in ["open", "high", "low", "close"]:
            provisional[column] *= factor
        source += "（按昨日复权系数换算）"

    combined = pd.concat([base, provisional], ignore_index=True)
    return combined, source


def format_report(results: list[SignalResult]) -> str:
    timestamp = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    lines = ["A股收盘策略信号", f"运行时间：{timestamp}", ""]
    if not results:
        lines.append("当前没有启用的自选配置。请先在本机网页的“自选信号”页面添加。")
    for result in results:
        name = result.name or result.symbol
        lines.extend(
            [
                f"{name}（{result.symbol}）· {STRATEGY_NAMES[result.strategy_id]}",
                f"信号：{result.action}",
            ]
        )
        if result.data_date:
            lines.append(f"数据日期：{result.data_date:%Y-%m-%d} · 数据源：{result.source}")
        if result.status == "预估":
            lines.append("数据口径：15:00 分钟行情聚合的临时日线，待正式日线确认。")
        if result.close is not None:
            lines.append(f"参考收盘价：{result.close:.3f}")
        lines.extend([f"原因：{result.reason}", ""])
    lines.extend(
        [
            f"查看或调整配置：{APP_URL}",
            "",
            "仅为策略信号，需人工确认；不自动下单，历史回测不代表未来表现。",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="运行本地自选配置并输出收盘策略信号")
    parser.add_argument("--retries", type=int, default=0, help="数据未更新时的额外重试次数")
    parser.add_argument("--retry-delay", type=int, default=300, help="每次重试间隔（秒）")
    args = parser.parse_args()

    results = run_once()
    for _ in range(max(args.retries, 0)):
        if not any(result.status == "等待数据" for result in results):
            break
        time.sleep(max(args.retry_delay, 0))
        results = run_once()
    print(format_report(results))


if __name__ == "__main__":
    main()
