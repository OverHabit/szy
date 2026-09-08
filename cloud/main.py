"""A股收盘策略信号 — 腾讯云 SCF 版。

零第三方依赖：只用 Python 标准库直接请求券商公开行情接口，不需要 akshare / pandas / numpy，
代码包不到 20KB，可直接上传云函数。

数据源：腾讯证券（前复权）→ 东方财富（前复权）→ 腾讯实时收盘行情（预估兜底）。
发信：QQ 邮箱 SMTP，凭据从环境变量读取，不写死在代码里。

用法：
    python main.py              # 打印报告（不发信）
    python main.py --send       # 打印并发信
云函数入口：main_handler(event, context)
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
import time
import urllib.request
from datetime import date, datetime, time as clock_time, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText

SHANGHAI = timezone(timedelta(hours=8))
APP_URL = "https://9s5ky5xuwk4ohrjj5sdk8t.streamlit.app"
MARKET_CLOSE = clock_time(15, 0)
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 与本机 SQLite 自选清单保持一致；本机改了清单，这里同步改一次即可。
WATCHLIST = [
    {"symbol": "000547", "name": "航天发展", "instrument_type": "stock"},
    {"symbol": "001283", "name": "豪鹏科技", "instrument_type": "stock"},
    {"symbol": "002050", "name": "三花智控", "instrument_type": "stock"},
    {"symbol": "002202", "name": "金风科技", "instrument_type": "stock"},
    {"symbol": "002270", "name": "华明装备", "instrument_type": "stock"},
    {"symbol": "588000", "name": "科创50ETF", "instrument_type": "etf"},
    {"symbol": "600029", "name": "南方航空", "instrument_type": "stock"},
    {"symbol": "600276", "name": "恒瑞医药", "instrument_type": "stock"},
    {"symbol": "600309", "name": "万华化学", "instrument_type": "stock"},
    {"symbol": "600879", "name": "航天电子", "instrument_type": "stock"},
    {"symbol": "603993", "name": "洛阳钼业", "instrument_type": "stock"},
]

ADJUST = "qfq"
SHORT_WINDOW = 10
LONG_WINDOW = 30
STRATEGY_LABEL = "双均线趋势"
ACTION_CHANGES = {"策略买入信号", "策略卖出信号"}
ACTION_MAP = {(0, 1): "策略买入信号", (1, 0): "策略卖出信号", (1, 1): "持有", (0, 0): "观望"}


def http_get(url: str, timeout: int = 15, retries: int = 2) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "ignore")
        except Exception as exc:  # 网络抖动时退避重试
            last = exc
            if attempt < retries:
                time.sleep(0.8)
    raise last  # type: ignore[misc]


def market_symbol(symbol: str, instrument_type: str) -> str:
    if instrument_type == "etf":
        return ("sh" if symbol[0] in "56" else "sz") + symbol
    if symbol[0] in "489":
        return "bj" + symbol
    return ("sh" if symbol[0] == "6" else "sz") + symbol


def fetch_tencent_daily(symbol: str, instrument_type: str, start: date, end: date):
    code = market_symbol(symbol, instrument_type)
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
        f"{code},day,{start:%Y-%m-%d},{end:%Y-%m-%d},640,{ADJUST}"
    )
    payload = json.loads(http_get(url))
    node = (payload.get("data") or {}).get(code) or {}
    rows = node.get("qfqday") or node.get("hfqday") or node.get("day") or []
    bars = [
        {
            "date": row[0],
            "open": float(row[1]),
            "close": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]
    return bars, "腾讯证券"


def fetch_eastmoney_daily(symbol: str, instrument_type: str, start: date, end: date):
    prefix = "1" if market_symbol(symbol, instrument_type).startswith("sh") else "0"
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={prefix}.{symbol}&ut=fa5fd1943c7b386f172d6893dbfba10b"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt=1&beg={start:%Y%m%d}&end={end:%Y%m%d}"
    )
    payload = json.loads(http_get(url))
    rows = (payload.get("data") or {}).get("klines") or []
    bars = [
        {
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
        }
        for parts in (row.split(",") for row in rows)
    ]
    return bars, "东方财富"


def fetch_daily(symbol: str, instrument_type: str, start: date, end: date):
    """依次尝试各数据源，返回最新的一份（谁有当日 K 线就用谁）。"""
    fallback = None
    for provider in (fetch_tencent_daily, fetch_eastmoney_daily):
        try:
            bars, source = provider(symbol, instrument_type, start, end)
        except Exception:
            continue
        if not bars:
            continue
        if bars[-1]["date"] == end.isoformat():
            return bars, source
        if fallback is None or bars[-1]["date"] > fallback[0][-1]["date"]:
            fallback = (bars, source)
    if fallback is None:
        raise ConnectionError("所有行情源均不可用")
    return fallback


def fetch_realtime(symbol: str, instrument_type: str, session: date):
    """取 15:00 后的实时收盘快照，作为当日正式日线未出时的预估。"""
    code = market_symbol(symbol, instrument_type)
    text = http_get(f"https://qt.gtimg.cn/q={code}")
    fields = text.split('"')[1].split("~")
    if len(fields) <= 34:
        raise ValueError("实时行情字段不完整")
    stamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S")
    if stamp.date() != session:
        raise ValueError("实时行情不是当前交易日")
    if stamp.time() < MARKET_CLOSE:
        raise ValueError("尚未到收盘时点")
    multiplier = 1.0 if instrument_type == "etf" else 100.0
    bar = {
        "date": session.isoformat(),
        "open": float(fields[5]),
        "close": float(fields[3]),
        "high": float(fields[33]),
        "low": float(fields[34]),
        "volume": float(fields[6]) * multiplier,
    }
    previous_close = float(fields[4])
    if min(bar["open"], bar["close"], bar["high"], bar["low"], bar["volume"]) <= 0:
        raise ValueError("实时行情存在无效报价")
    label = "腾讯证券 ETF 实时行情" if instrument_type == "etf" else "腾讯证券 A 股实时行情"
    return bar, label, previous_close, stamp


def is_trading_day(today: date) -> bool:
    """周末直接跳过；节假日用上证指数实时行情的日期戳判断。"""
    if today.weekday() >= 5:
        return False
    try:
        text = http_get("https://qt.gtimg.cn/q=sh000001")
        stamp = datetime.strptime(text.split('"')[1].split("~")[30], "%Y%m%d%H%M%S")
        return stamp.date() == today
    except Exception:
        return True


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def evaluate(entry: dict, today: date) -> dict:
    start = today - timedelta(days=550)
    bars, source = fetch_daily(entry["symbol"], entry["instrument_type"], start, today)
    status = "成功"
    realtime_at = None

    reason_suffix = ""
    if not bars or bars[-1]["date"] != today.isoformat():
        try:
            bar, label, previous_close, stamp = fetch_realtime(
                entry["symbol"], entry["instrument_type"], today
            )
            base = [item for item in bars if item["date"] < today.isoformat()]
            if not base:
                raise ValueError("缺少昨日及以前的日线，无法换算复权系数")
            factor = float(base[-1]["close"]) / previous_close
            for key in ("open", "high", "low", "close"):
                bar[key] *= factor
            bars = base + [bar]
            source = label + "（按昨日复权系数换算）"
            status = "预估"
            realtime_at = stamp
            reason_suffix = " 当日正式日线尚未更新，按实时收盘行情预估，待正式日线确认。"
        except Exception:
            # 实时行情也拿不到，就照本机脚本的做法：用上一交易日日线算，并明确标注口径。
            status = "上一交易日" if bars else "等待数据"
            reason_suffix = (
                " 当日正式日线与实时行情均不可用，按上一交易日日线计算。" if bars else ""
            )

    if not bars or len(bars) < LONG_WINDOW + 1:
        return {
            "symbol": entry["symbol"],
            "name": entry["name"],
            "action": "数据尚未更新",
            "close": float(bars[-1]["close"]) if bars else None,
            "reason": f"历史K线不足 {LONG_WINDOW + 1} 条。{reason_suffix}".strip(),
            "status": "等待数据",
            "data_date": bars[-1]["date"] if bars else None,
            "source": source,
            "realtime_at": None,
        }

    closes = [float(item["close"]) for item in bars]
    short_now, long_now = mean(closes[-SHORT_WINDOW:]), mean(closes[-LONG_WINDOW:])
    short_prev = mean(closes[-SHORT_WINDOW - 1 : -1])
    long_prev = mean(closes[-LONG_WINDOW - 1 : -1])
    action = ACTION_MAP[(1 if short_prev > long_prev else 0, 1 if short_now > long_now else 0)]
    reason = (
        f"短期均线 {short_now:.2f} {'高于' if short_now > long_now else '不高于'} "
        f"长期均线 {long_now:.2f}。{reason_suffix}"
    ).replace(" 。", "。")
    return {
        "symbol": entry["symbol"],
        "name": entry["name"],
        "action": action,
        "close": closes[-1],
        "reason": reason,
        "status": status,
        "data_date": bars[-1]["date"],
        "source": source,
        "realtime_at": realtime_at,
    }


def format_report(results: list[dict]) -> str:
    now = datetime.now(SHANGHAI)
    lines = ["A股收盘策略信号", f"运行时间：{now:%Y-%m-%d %H:%M}", ""]
    changed = [item for item in results if item["action"] in ACTION_CHANGES]
    if changed:
        summary = "；".join(
            f"{item['name']}（{item['symbol']}）{item['action']}" for item in changed
        )
        lines.extend([f"今日信号变化：{summary}", ""])
    elif results:
        lines.extend(["今日信号变化：无买入或卖出，全部为持有或观望。", ""])

    provisional = [item for item in results if item["status"] == "预估"]
    delayed = [item for item in results if item["status"] in {"上一交易日", "缓存数据"}]
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

    for item in results:
        lines.extend(
            [
                f"{item['name']}（{item['symbol']}）· {STRATEGY_LABEL}",
                f"信号：{item['action']}",
            ]
        )
        if item["data_date"]:
            lines.append(f"数据日期：{item['data_date']} · 数据源：{item['source']}")
        if item["realtime_at"]:
            lines.append(f"实时价格时刻：{item['realtime_at']:%Y-%m-%d %H:%M:%S}")
        if item["status"] == "预估":
            lines.append("数据口径：15:00 后实时收盘行情预估，待正式日线确认。")
        elif item["status"] == "上一交易日":
            lines.append("数据口径：正式当日日线尚未更新，按上一交易日日线计算。")
        if item["close"] is not None:
            lines.append(f"参考收盘价：{item['close']:.3f}")
        lines.extend([f"原因：{item['reason']}", ""])
    lines.extend(
        [
            f"查看或调整配置：{APP_URL}",
            "",
            "仅为策略信号，需人工确认；不自动下单，历史回测不代表未来表现。",
        ]
    )
    return "\n".join(lines)


def send_mail(subject: str, body: str) -> bool:
    user = os.environ.get("SMTP_USER", "")
    code = os.environ.get("SMTP_AUTH_CODE", "")
    recipient = os.environ.get("MAIL_TO", "")
    if not user or not code or not recipient:
        print("未配置 SMTP_USER / SMTP_AUTH_CODE / MAIL_TO，跳过发信。")
        return False
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = Header(subject, "utf-8")
    message["From"] = user
    message["To"] = recipient
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=20) as server:
        server.login(user, code)
        server.sendmail(user, [recipient], message.as_string())
    return True


def run(send: bool = False) -> int:
    today = datetime.now(SHANGHAI).date()
    if not is_trading_day(today):
        print(f"今日（{today:%Y-%m-%d}）为非交易日，未生成信号。")
        return 10

    results = []
    for entry in WATCHLIST:
        try:
            results.append(evaluate(entry, today))
        except Exception as exc:
            results.append(
                {
                    "symbol": entry["symbol"],
                    "name": entry["name"],
                    "action": "数据尚未更新",
                    "close": None,
                    "reason": f"行情获取失败：{type(exc).__name__}: {exc}",
                    "status": "等待数据",
                    "data_date": None,
                    "source": None,
                    "realtime_at": None,
                }
            )

    report = format_report(results)
    print(report)
    if send:
        send_mail(f"A股收盘策略信号 {today:%Y-%m-%d}", report)
    return 0


def main_handler(event, context):  # 云函数入口
    return {"exit_code": run(send=True), "time": f"{datetime.now(SHANGHAI):%Y-%m-%d %H:%M:%S}"}


if __name__ == "__main__":
    sys.exit(run(send="--send" in sys.argv))
