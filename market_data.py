from __future__ import annotations

from datetime import date, time as clock_time
from pathlib import Path
import time
from typing import Callable, Iterable

import akshare as ak
import pandas as pd


CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "market_data"

COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover_rate",
}

HistoryProvider = Callable[[str, date, date, str], pd.DataFrame]
MinuteProvider = Callable[[str, date], pd.DataFrame]


def fetch_a_share_universe() -> pd.DataFrame:
    raw = pd.DataFrame()
    last_error: Exception | None = None
    sources = [ak.stock_info_a_code_name, ak.stock_zh_a_spot_em]
    for source in sources:
        for attempt in range(2):
            try:
                raw = source()
                if not raw.empty:
                    break
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.5)
        if not raw.empty:
            break

    if raw.empty:
        raise ConnectionError(
            "股票名称列表暂时无法连接，请稍后重试或直接输入 6 位股票代码"
        ) from last_error

    code_column = "code" if "code" in raw.columns else "代码"
    name_column = "name" if "name" in raw.columns else "名称"
    if code_column not in raw.columns or name_column not in raw.columns:
        raise ValueError("AKShare 返回的 A 股代码表字段异常")

    universe = raw[[code_column, name_column]].copy()
    universe.columns = ["code", "name"]
    universe["code"] = universe["code"].astype(str).str.zfill(6)
    universe["name"] = universe["name"].astype(str).str.strip()
    return universe.drop_duplicates(subset=["code"]).reset_index(drop=True)


def resolve_a_share(
    query: str, universe: pd.DataFrame | None = None
) -> tuple[str, str]:
    query = query.strip()
    if not query:
        raise ValueError("请输入股票代码或名称")

    if query.isdigit():
        if len(query) != 6:
            raise ValueError("股票代码应为 6 位数字，例如 600519")
        if universe is None:
            return query, query

        stocks = universe.copy()
        stocks["code"] = stocks["code"].astype(str).str.zfill(6)
        stocks["name"] = stocks["name"].astype(str).str.strip()
        matches = stocks[stocks["code"] == query]
        if matches.empty:
            raise ValueError(f"未找到股票代码 {query}")
        row = matches.iloc[0]
        return str(row["code"]), str(row["name"])

    stocks = fetch_a_share_universe() if universe is None else universe.copy()
    stocks["code"] = stocks["code"].astype(str).str.zfill(6)
    stocks["name"] = stocks["name"].astype(str).str.strip()

    exact = stocks[stocks["name"].str.casefold() == query.casefold()]
    if len(exact) == 1:
        row = exact.iloc[0]
        return str(row["code"]), str(row["name"])
    if len(exact) > 1:
        choices = "、".join(
            f"{row['name']}（{row['code']}）" for _, row in exact.head(5).iterrows()
        )
        raise ValueError(f"存在多个同名股票，请改用代码：{choices}")

    fuzzy = stocks[
        stocks["name"].str.contains(query, case=False, regex=False, na=False)
    ]
    if fuzzy.empty:
        raise ValueError(f"未找到名称为“{query}”的 A 股")

    suggestions = "、".join(
        f"{row['name']}（{row['code']}）" for _, row in fuzzy.head(5).iterrows()
    )
    raise ValueError(f"请输入完整股票名称。你可能想找：{suggestions}")


def fetch_a_share_history(
    symbol: str,
    start_date: date,
    end_date: date,
    adjust: str = "qfq",
    *,
    instrument_type: str = "stock",
    cache_dir: Path | None = None,
    providers: Iterable[tuple[str, HistoryProvider]] | None = None,
) -> pd.DataFrame:
    """Fetch daily history for a stock or exchange-traded fund.

    ``stock`` uses the existing A-share provider chain. ``etf`` uses the
    separate ETF endpoints exposed by AKShare; ETF codes cannot be queried
    reliably through the A-share endpoints.
    """
    symbol = symbol.strip()
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError("股票代码应为 6 位数字，例如 600519")
    if start_date >= end_date:
        raise ValueError("开始日期必须早于结束日期")
    if instrument_type not in {"stock", "etf"}:
        raise ValueError("标的类型必须为 stock 或 etf")

    target_cache_dir = CACHE_DIR if cache_dir is None else Path(cache_dir)
    cached = _read_history_cache(target_cache_dir, symbol, adjust, instrument_type)
    active_providers = (
        list(providers)
        if providers is not None
        else _default_history_providers(instrument_type)
    )
    failures: list[str] = []

    for source_name, provider in active_providers:
        for attempt in range(2):
            try:
                raw = provider(symbol, start_date, end_date, adjust)
                data = _normalise_history(raw)
                if data.empty:
                    raise ValueError("未返回有效行情")

                merged = _merge_history(cached, data)
                _write_history_cache(
                    target_cache_dir, symbol, adjust, instrument_type, merged
                )
                result = _filter_history(data, start_date, end_date)
                if result.empty:
                    raise ValueError("指定日期范围内没有交易数据")
                result.attrs["source"] = source_name
                result.attrs["stale"] = False
                return result
            except Exception as exc:
                if attempt == 1:
                    failures.append(f"{source_name}: {type(exc).__name__}")
                else:
                    time.sleep(0.5)

    cached_result = _filter_history(cached, start_date, end_date)
    if not cached_result.empty:
        latest = cached_result["date"].max().strftime("%Y-%m-%d")
        cached_result.attrs["source"] = f"本地缓存（更新至 {latest}）"
        cached_result.attrs["stale"] = True
        return cached_result

    details = "；".join(failures) if failures else "没有可用数据源"
    raise ConnectionError(
        f"所有免费行情源均暂时不可用（{details}）。请稍后重试"
    )


def fetch_provisional_daily_bar(
    symbol: str,
    session_date: date,
    instrument_type: str,
    *,
    providers: Iterable[tuple[str, MinuteProvider]] | None = None,
) -> pd.DataFrame:
    """Build one provisional daily bar from same-day one-minute data.

    The bar is only returned when the provider includes the 15:00 close. It
    is deliberately not cached because the official daily bar remains the
    source of record once it becomes available.
    """
    symbol = symbol.strip()
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError("股票或 ETF 代码必须为 6 位数字")
    if instrument_type not in {"stock", "etf"}:
        raise ValueError("标的类型必须为 stock 或 etf")

    active_providers = (
        list(providers)
        if providers is not None
        else _default_minute_providers(instrument_type)
    )
    failures: list[str] = []
    for source_name, provider in active_providers:
        try:
            minutes = _normalise_minute_history(provider(symbol, session_date))
            bar = _aggregate_minutes_to_daily(minutes, session_date)
            bar.attrs["source"] = source_name
            return bar
        except Exception as exc:
            failures.append(f"{source_name}: {type(exc).__name__}")
    details = "；".join(failures) if failures else "没有可用分钟数据源"
    raise ConnectionError(f"无法取得包含 15:00 收盘的分钟行情（{details}）")


def _default_history_providers(
    instrument_type: str = "stock",
) -> list[tuple[str, HistoryProvider]]:
    if instrument_type == "etf":
        return [
            ("东方财富 ETF", _fetch_etf_history_eastmoney),
            ("新浪财经 ETF（不复权）", _fetch_etf_history_sina),
        ]
    return [
        ("东方财富", _fetch_history_eastmoney),
        ("腾讯证券", _fetch_history_tencent),
        ("新浪财经", _fetch_history_sina),
    ]


def _default_minute_providers(
    instrument_type: str,
) -> list[tuple[str, MinuteProvider]]:
    if instrument_type == "etf":
        return [("东方财富 ETF 分钟线", _fetch_etf_minute_eastmoney)]
    return [("东方财富 A 股分钟线", _fetch_stock_minute_eastmoney)]


def _fetch_history_eastmoney(
    symbol: str, start_date: date, end_date: date, adjust: str
) -> pd.DataFrame:
    return ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust=adjust,
    )


def _fetch_history_tencent(
    symbol: str, start_date: date, end_date: date, adjust: str
) -> pd.DataFrame:
    return ak.stock_zh_a_hist_tx(
        symbol=_market_symbol(symbol),
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust=adjust,
        timeout=15,
    )


def _fetch_history_sina(
    symbol: str, start_date: date, end_date: date, adjust: str
) -> pd.DataFrame:
    return ak.stock_zh_a_daily(
        symbol=_market_symbol(symbol),
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust=adjust,
    )


def _fetch_etf_history_eastmoney(
    symbol: str, start_date: date, end_date: date, adjust: str
) -> pd.DataFrame:
    return ak.fund_etf_hist_em(
        symbol=symbol,
        period="daily",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust=adjust,
    )


def _fetch_etf_history_sina(
    symbol: str, start_date: date, end_date: date, adjust: str
) -> pd.DataFrame:
    del start_date, end_date, adjust
    return ak.fund_etf_hist_sina(symbol=_market_symbol(symbol))


def _fetch_stock_minute_eastmoney(symbol: str, session_date: date) -> pd.DataFrame:
    start = f"{session_date:%Y-%m-%d} 09:30:00"
    end = f"{session_date:%Y-%m-%d} 15:05:00"
    return ak.stock_zh_a_hist_min_em(
        symbol=symbol,
        start_date=start,
        end_date=end,
        period="1",
        adjust="",
    )


def _fetch_etf_minute_eastmoney(symbol: str, session_date: date) -> pd.DataFrame:
    start = f"{session_date:%Y-%m-%d} 09:30:00"
    end = f"{session_date:%Y-%m-%d} 15:05:00"
    return ak.fund_etf_hist_min_em(
        symbol=symbol,
        start_date=start,
        end_date=end,
        period="1",
        adjust="",
    )


def _market_symbol(symbol: str) -> str:
    if symbol.startswith(("4", "8", "9")):
        return f"bj{symbol}"
    if symbol.startswith("6"):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _normalise_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume"]
        )

    data = raw.rename(columns=COLUMN_MAP).copy()
    needed = ["date", "open", "high", "low", "close", "volume"]
    missing = set(needed).difference(data.columns)
    if missing:
        raise ValueError(f"AKShare 返回字段异常: {', '.join(sorted(missing))}")

    data["date"] = pd.to_datetime(data["date"])
    for column in ["open", "high", "low", "close", "volume"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return (
        data.dropna(subset=needed)
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _normalise_minute_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("未返回分钟行情")
    data = raw.rename(columns={**COLUMN_MAP, "时间": "timestamp"}).copy()
    needed = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = set(needed).difference(data.columns)
    if missing:
        raise ValueError(f"分钟行情缺少字段: {', '.join(sorted(missing))}")
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    for column in ["open", "high", "low", "close", "volume"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.dropna(subset=needed).sort_values("timestamp").reset_index(drop=True)


def _aggregate_minutes_to_daily(minutes: pd.DataFrame, session_date: date) -> pd.DataFrame:
    session = minutes.loc[minutes["timestamp"].dt.date == session_date].copy()
    if session.empty:
        raise ValueError("分钟行情不包含当天数据")
    session = session.sort_values("timestamp").reset_index(drop=True)
    if session["timestamp"].iloc[-1].time() < clock_time(15, 0):
        raise ValueError("分钟行情尚未包含 15:00 收盘数据")
    opening = session.loc[session["open"] > 0, "open"]
    open_price = float(opening.iloc[0]) if not opening.empty else float(session["close"].iloc[0])
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(session_date),
                "open": open_price,
                "high": float(session["high"].max()),
                "low": float(session["low"].min()),
                "close": float(session["close"].iloc[-1]),
                "volume": float(session["volume"].sum()),
            }
        ]
    )


def _cache_path(
    cache_dir: Path, symbol: str, adjust: str, instrument_type: str = "stock"
) -> Path:
    adjust_name = adjust or "raw"
    return cache_dir / f"{instrument_type}_{symbol}_{adjust_name}.csv"


def _read_history_cache(
    cache_dir: Path, symbol: str, adjust: str, instrument_type: str = "stock"
) -> pd.DataFrame:
    path = _cache_path(cache_dir, symbol, adjust, instrument_type)
    if not path.exists():
        return pd.DataFrame()
    try:
        return _normalise_history(pd.read_csv(path))
    except Exception:
        return pd.DataFrame()


def _write_history_cache(
    cache_dir: Path,
    symbol: str,
    adjust: str,
    instrument_type: str,
    data: pd.DataFrame,
) -> None:
    if data.empty:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _cache_path(cache_dir, symbol, adjust, instrument_type)
        temporary = path.with_suffix(".tmp")
        data.to_csv(temporary, index=False, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def _merge_history(cached: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    if cached.empty:
        return fresh.copy()
    return (
        pd.concat([cached, fresh], ignore_index=True)
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _filter_history(
    data: pd.DataFrame, start_date: date, end_date: date
) -> pd.DataFrame:
    if data.empty or "date" not in data.columns:
        return pd.DataFrame()
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    return data.loc[data["date"].between(start, end)].reset_index(drop=True)
