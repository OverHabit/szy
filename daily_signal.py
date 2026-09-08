"""Create an email-ready daily report from the locally managed watchlist.

This script has no email password and never places an order. Hermes runs it as
a script-only task, then delivers its standard output through the already
configured email channel.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
import time
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from market_data import fetch_a_share_history, fetch_realtime_daily_bar
from quant_core import run_ma_crossover, run_volume_breakout
from watchlist_store import DATABASE_PATH, list_watchlist


APP_URL = "https://9s5ky5xuwk4ohrjj5sdk8t.streamlit.app"
STRATEGY_NAMES = {"ma_crossover": "双均线趋势", "volume_breakout": "放量突破"}
SHANGHAI = ZoneInfo("Asia/Shanghai")

OFFICIAL_STATUS = "成功"
ACTION_CHANGES = {"策略买入信号", "策略卖出信号"}
MARKET_CLOSE = clock_time(15, 0)
CALENDAR_CACHE = Path(__file__).resolve().parent / ".cache" / "trade_calendar.csv"
EXIT_SKIP = 10

# Higher means closer to the official daily bar of the trading day.
STATUS_RANK = {"等待数据": 0, "上一交易日": 1, "缓存数据": 1, "预估": 2, "成功": 3}


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
    realtime_at: datetime | None = None


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
    realtime_fetcher: Callable[[str, date, str], pd.DataFrame] = fetch_realtime_daily_bar,
) -> list[SignalResult]:
    """Fetch and evaluate each enabled configuration once.

    Formal daily bars always win. If today's bar is delayed, use a post-close
    real-time quote as a clearly labelled provisional bar.
    """
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
                require_date=today,
            )
            latest_date = pd.Timestamp(prices["date"].iloc[-1]).date()
            source = str(prices.attrs.get("source", "未知数据源"))
            if not bool(prices.attrs.get("stale", False)) and latest_date == today:
                evaluated = evaluate_entry(entry, prices)
                results.append(replace(evaluated, source=source, data_date=latest_date))
                continue

            try:
                realtime = realtime_fetcher(
                    entry["symbol"], today, entry["instrument_type"]
                )
                provisional, provisional_source = _with_realtime_bar(
                    entry, prices, realtime, today, start, fetcher
                )
                evaluated = evaluate_entry(entry, provisional)
                results.append(
                    replace(
                        evaluated,
                        status="预估",
                        data_date=today,
                        source=provisional_source,
                        realtime_at=_realtime_timestamp(realtime),
                        reason=f"{evaluated.reason} 当日正式日线尚未更新，按实时收盘行情预估，待正式日线确认。",
                    )
                )
            except Exception as realtime_error:
                evaluated = evaluate_entry(entry, prices)
                status = "上一交易日" if latest_date < today else "缓存数据"
                suffix = (
                    f"正式当日日线和实时行情均未可用，按 {latest_date:%Y-%m-%d} 日线计算。"
                    if latest_date < today
                    else "本次未取得新的正式日线或实时行情，按本地缓存日线计算。"
                )
                results.append(
                    replace(
                        evaluated,
                        status=status,
                        source=source,
                        data_date=latest_date,
                        reason=f"{evaluated.reason} {suffix} 实时行情错误：{type(realtime_error).__name__}。",
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


def _realtime_timestamp(realtime: pd.DataFrame) -> datetime | None:
    if "quote_timestamp" not in realtime.columns or realtime.empty:
        return None
    timestamp = pd.to_datetime(realtime["quote_timestamp"].iloc[-1], errors="coerce")
    return None if pd.isna(timestamp) else timestamp.to_pydatetime()


def _with_realtime_bar(
    entry: dict[str, Any],
    prices: pd.DataFrame,
    realtime: pd.DataFrame,
    today: date,
    start: date,
    fetcher: Callable[..., pd.DataFrame],
) -> tuple[pd.DataFrame, str]:
    base = prices.loc[pd.to_datetime(prices["date"]).dt.date < today].copy()
    if base.empty:
        raise ValueError("缺少昨日及以前的日线，无法计算实时预估")
    source = str(realtime.attrs.get("source", "实时行情"))
    provisional = realtime.copy()
    if entry["adjust"]:
        previous_quote = pd.to_numeric(
            provisional.get("previous_close", pd.Series(dtype=float)), errors="coerce"
        )
        if not previous_quote.empty and float(previous_quote.iloc[0]) > 0:
            previous_raw_close = float(previous_quote.iloc[0])
        else:
            raw = fetcher(
                entry["symbol"], start, today, "", instrument_type=entry["instrument_type"]
            )
            previous_date = pd.Timestamp(base["date"].iloc[-1]).date()
            raw_previous = raw.loc[pd.to_datetime(raw["date"]).dt.date == previous_date]
            if raw_previous.empty or float(raw_previous["close"].iloc[-1]) <= 0:
                raise ValueError("无法取得与复权日线对应的昨日原始价格")
            previous_raw_close = float(raw_previous["close"].iloc[-1])
        factor = float(base["close"].iloc[-1]) / previous_raw_close
        for column in ["open", "high", "low", "close"]:
            provisional[column] *= factor
        source += "（按昨日复权系数换算）"
    return pd.concat([base, provisional], ignore_index=True), source


def is_official(result: SignalResult, today: date) -> bool:
    """True only when the signal came from the official daily bar of ``today``."""
    return result.status == OFFICIAL_STATUS and result.data_date == today


def all_official(
    results: list[SignalResult], today: date, *, now: datetime | None = None
) -> bool:
    """Before the close no bar can count as today's official daily bar."""
    if not results:
        return False
    moment = now or datetime.now(SHANGHAI)
    if moment.time() < MARKET_CLOSE:
        return False
    return all(is_official(result, today) for result in results)


def merge_best(
    previous: list[SignalResult], current: list[SignalResult]
) -> list[SignalResult]:
    """Keep the closest-to-official result seen so far for each symbol."""
    if not previous:
        return list(current)
    best = {result.symbol: result for result in previous}
    for result in current:
        known = best.get(result.symbol)
        if known is None or STATUS_RANK.get(result.status, 0) >= STATUS_RANK.get(
            known.status, 0
        ):
            best[result.symbol] = result
    order: dict[str, int] = {}
    for result in list(previous) + list(current):
        order.setdefault(result.symbol, len(order))
    return sorted(best.values(), key=lambda result: order[result.symbol])


def load_trade_calendar() -> set[date] | None:
    """Return the exchange trading calendar, or None when it cannot be fetched."""
    if CALENDAR_CACHE.exists():
        try:
            frame = pd.read_csv(CALENDAR_CACHE)
            return _calendar_from(frame["trade_date"])
        except Exception:
            pass
    try:
        import akshare as ak

        frame = ak.tool_trade_date_hist_sina()
    except Exception:
        return None
    try:
        CALENDAR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(CALENDAR_CACHE, index=False)
    except OSError:
        pass
    return _calendar_from(frame["trade_date"])


def _calendar_from(values: Iterable[Any]) -> set[date]:
    days: set[date] = set()
    for value in values:
        try:
            days.add(datetime.strptime(str(value)[:10], "%Y-%m-%d").date())
        except ValueError:
            continue
    return days


def is_trading_day(day: date) -> bool:
    """Treat Monday to Friday as a trading day when the calendar is unavailable."""
    calendar = load_trade_calendar()
    if calendar is None:
        return day.weekday() < 5
    return day in calendar


def parse_deadline(value: str, today: date) -> datetime:
    hour, _, minute = value.partition(":")
    return datetime.combine(
        today, clock_time(int(hour), int(minute or 0)), tzinfo=SHANGHAI
    )


def format_report(results: list[SignalResult]) -> str:
    timestamp = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    lines = ["A股收盘策略信号", f"运行时间：{timestamp}", ""]
    changed = [result for result in results if result.action in ACTION_CHANGES]
    if changed:
        summary = "；".join(
            f"{result.name or result.symbol}（{result.symbol}）{result.action}"
            for result in changed
        )
        lines.extend([f"今日信号变化：{summary}", ""])
    elif results:
        lines.extend(["今日信号变化：无买入或卖出，全部为持有或观望。", ""])
    provisional = [result for result in results if result.status == "预估"]
    delayed = [
        result for result in results if result.status in {"上一交易日", "缓存数据"}
    ]
    if provisional:
        lines.append(
            f"数据口径：{len(provisional)}/{len(results)} 个标的当日正式日线未更新，"
            "按实时收盘价预估，已在下方逐条标注。"
        )
    if delayed:
        lines.append(
            f"数据口径：{len(delayed)}/{len(results)} 个标的当日日线与实时行情均未更新，"
            "按上一交易日日线计算，已在下方逐条标注。"
        )
    if provisional or delayed:
        lines.append("")
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
        if result.realtime_at:
            lines.append(f"实时价格时刻：{result.realtime_at:%Y-%m-%d %H:%M:%S}")
        if result.status == "预估":
            lines.append("数据口径：15:00 后实时收盘行情预估，待正式日线确认。")
        elif result.status == "上一交易日":
            lines.append("数据口径：正式当日日线尚未更新，按上一交易日日线计算。")
        elif result.status == "缓存数据":
            lines.append("数据口径：本次未取得新的正式日线，按本地缓存日线计算。")
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
    parser.add_argument("--retries", type=int, default=40, help="等待正式日线的重试次数")
    parser.add_argument("--retry-delay", type=int, default=300, help="每次重试间隔（秒）")
    parser.add_argument(
        "--deadline", default="18:00", help="本地时间 HH:MM，超过后不再等待正式日线"
    )
    parser.add_argument(
        "--on-timeout",
        choices=["send", "skip"],
        default="send",
        help="到截止时间仍未取得全部正式日线时的处理方式",
    )
    parser.add_argument(
        "--no-wait", action="store_true", help="只跑一轮，不等待正式日线"
    )
    args = parser.parse_args()

    today = datetime.now(SHANGHAI).date()
    if not is_trading_day(today):
        print(f"A股收盘策略信号\n运行时间：{datetime.now(SHANGHAI):%Y-%m-%d %H:%M}\n")
        print(f"今日（{today:%Y-%m-%d}）为非交易日，未生成信号。")
        raise SystemExit(EXIT_SKIP)

    results = run_once(run_date=today)
    if not args.no_wait and results and not all_official(results, today):
        results = wait_for_official(
            results,
            today,
            retries=max(args.retries, 0),
            retry_delay=max(args.retry_delay, 1),
            deadline=parse_deadline(args.deadline, today),
        )

    if results and not all_official(results, today) and args.on_timeout == "skip":
        pending = [
            f"{result.name or result.symbol}（{result.symbol}）"
            for result in results
            if not is_official(result, today)
        ]
        print(f"A股收盘策略信号\n运行时间：{datetime.now(SHANGHAI):%Y-%m-%d %H:%M}\n")
        print(f"以下标的在 {args.deadline} 前仍未取得正式日线，本次不发送：")
        print("、".join(pending))
        raise SystemExit(EXIT_SKIP)

    print(format_report(results))


def wait_for_official(
    results: list[SignalResult],
    today: date,
    *,
    retries: int,
    retry_delay: int,
    deadline: datetime,
) -> list[SignalResult]:
    """Re-poll until every symbol has the official daily bar or time runs out.

    Distributors publish the same-day bar at different times, so a symbol that
    is still provisional on one pass may be official on the next one.
    """
    for _ in range(retries):
        if all_official(results, today):
            break
        now = datetime.now(SHANGHAI)
        if now >= deadline:
            break
        remaining = (deadline - now).total_seconds()
        time.sleep(min(retry_delay, max(remaining, 1)))
        results = merge_best(results, run_once(run_date=today))
    return results


if __name__ == "__main__":
    main()
