"""Streamlit interface for changing the configurable watchlist."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st

from watchlist_store import (
    DEFAULT_PARAMETERS,
    delete_watchlist_entry,
    list_watchlist,
    save_watchlist_entry,
)
from watchlist_sync import describe, ensure_synced, mark_pending, sync_available


if TYPE_CHECKING:
    from daily_signal import SignalResult


STRATEGIES = {
    "ma_crossover": "双均线趋势",
    "volume_breakout": "放量突破",
}

SYNC_MIN_INTERVAL = 60  # 秒；同一标签页内的重复 rerun 不打 GitHub API


def render_watchlist_page() -> None:
    if not _password_gate():
        return

    st.title("自选信号")
    st.caption("在此调整股票、策略和参数。清单会同步到云端，收盘信号与本机页面共用同一份配置。")

    _sync_once_per_session()
    notice = st.session_state.pop("watchlist_notice", None)
    if notice:
        st.success(notice)

    entries = list_watchlist()
    enabled_count = sum(bool(entry["enabled"]) for entry in entries)
    st.subheader(f"已配置 {len(entries)} 项，其中启用 {enabled_count} 项")
    _render_sync_line()
    if entries:
        overview = pd.DataFrame(
            [
                {
                    "代码": entry["symbol"],
                    "名称": entry["name"] or "—",
                    "类型": "ETF" if entry["instrument_type"] == "etf" else "A 股",
                    "策略": STRATEGIES[entry["strategy_id"]],
                    "状态": "启用" if entry["enabled"] else "暂停",
                }
                for entry in entries
            ]
        )
        st.dataframe(overview, width="stretch", hide_index=True)

    st.subheader("立即运行")
    st.caption("按当前启用的配置获取行情并计算一次信号。此操作只在网页展示结果，不发送邮件。")
    if st.button("立即运行已启用自选", type="primary", width="stretch", disabled=not enabled_count):
        from daily_signal import run_once

        with st.spinner("正在获取行情并计算信号，请稍候…"):
            st.session_state["watchlist_signal_results"] = _signal_results_frame(run_once())

    results = st.session_state.get("watchlist_signal_results")
    if isinstance(results, pd.DataFrame):
        st.subheader("本次运行结果")
        st.dataframe(
            results,
            width="stretch",
            hide_index=True,
            column_config={
                "参考收盘价": st.column_config.NumberColumn(format="%.3f"),
            },
        )
        if "状态" in results and (results["状态"] == "预估").any():
            st.info("“预估”表示正式日线尚未更新，已使用 15:00 后实时收盘行情计算；正式日线到位后会优先采用正式数据。")
        if "状态" in results and (results["状态"] == "等待数据").any():
            st.warning("部分标的尚无可用日线或实时行情。可稍后再试。")

    with st.expander("添加股票或 ETF 策略", expanded=not entries):
        _entry_form("new", {**DEFAULT_PARAMETERS, "enabled": True, "name": "", "symbol": "", "instrument_type": "stock", "strategy_id": "ma_crossover"})

    st.subheader("编辑已有配置")
    for entry in entries:
        display_name = entry["name"] or entry["symbol"]
        with st.expander(f"{display_name}（{entry['symbol']}）· {STRATEGIES[entry['strategy_id']]}"):
            _entry_form(str(entry["id"]), entry)

    st.caption("研究提示：参数与历史回测仅供研究参考，不构成投资建议。")


def _password_gate() -> bool:
    """可选访问密码：只有配置了 WATCHLIST_PAGE_PASSWORD 才生效。

    网页端是公开链接，而清单现在会真正影响收盘信号，必要时给自己留一道门。
    """
    try:
        expected = str(st.secrets.get("WATCHLIST_PAGE_PASSWORD") or "")
    except Exception:
        expected = ""
    if not expected or st.session_state.get("watchlist_authed"):
        return True

    st.subheader("访问受限")
    entered = st.text_input("访问密码", type="password", key="watchlist_password_input")
    if entered:
        if entered == expected:
            st.session_state["watchlist_authed"] = True
            st.rerun()
        st.error("密码不正确。")
    return False


def _sync_once_per_session() -> None:
    """进入页面时与云端对一次账，60 秒内不重复请求。"""
    now = time.time()
    if now - float(st.session_state.get("watchlist_sync_at") or 0) < SYNC_MIN_INTERVAL:
        return
    st.session_state["watchlist_sync_at"] = now
    st.session_state["watchlist_sync_message"] = describe(ensure_synced())


def _render_sync_line() -> None:
    message = st.session_state.get("watchlist_sync_message")
    if not sync_available():
        st.warning("同步未启用：云端未配置 WATCHLIST_SYNC_TOKEN。本页的修改只停留在当前容器，不会进入收盘信号。")
    elif message:
        st.caption(f"云端同步 · {message}")


def _signal_results_frame(results: list[SignalResult]) -> pd.DataFrame:
    """Prepare runner output for a compact, readable Streamlit table."""
    return pd.DataFrame(
        [
            {
                "代码": result.symbol,
                "名称": result.name or "—",
                "策略": STRATEGIES.get(result.strategy_id, result.strategy_id),
                "信号": result.action,
                "状态": result.status,
                "数据日期": result.data_date.isoformat() if result.data_date else "—",
                "实时价格时刻": result.realtime_at.strftime("%Y-%m-%d %H:%M:%S") if result.realtime_at else "—",
                "参考收盘价": result.close,
                "数据源": result.source or "—",
                "说明": result.reason,
            }
            for result in results
        ]
    )


def _entry_form(form_key: str, entry: dict[str, Any]) -> None:
    with st.form(f"watchlist_entry_{form_key}"):
        left, right = st.columns(2)
        symbol = left.text_input(
            "股票或 ETF 代码", value=str(entry.get("symbol", "")),
            help="填写 6 位代码，例如 588000。",
        )
        name = right.text_input(
            "显示名称（可选）", value=str(entry.get("name", "")),
            help="用于网页和邮件显示；不影响行情查询。",
        )
        instrument_type = st.selectbox(
            "标的类型",
            options=["stock", "etf"],
            format_func=lambda value: "普通 A 股" if value == "stock" else "ETF",
            index=["stock", "etf"].index(entry.get("instrument_type", "stock")),
            help="ETF 会使用专用 ETF 日线接口；普通股票使用 A 股行情接口。",
        )
        enabled = st.checkbox("启用此项配置", value=bool(entry.get("enabled", True)))
        strategy_id = st.selectbox(
            "策略", options=list(STRATEGIES),
            format_func=lambda key: STRATEGIES[key],
            index=list(STRATEGIES).index(entry.get("strategy_id", "ma_crossover")),
            help="一只股票可分别添加多个策略，形成多条独立的信号配置。",
        )
        adjust_label = st.selectbox(
            "价格处理", ["前复权", "不复权", "后复权"],
            index={"qfq": 0, "": 1, "hfq": 2}.get(str(entry.get("adjust", "qfq")), 0),
            help="前复权适合连续收益和技术指标计算；日线策略通常使用前复权。",
        )
        values = _parameter_inputs(strategy_id, entry, form_key)
        fee_bps = st.number_input(
            "单边费用（基点）", min_value=0.0, max_value=50.0,
            value=float(entry.get("fee_bps", DEFAULT_PARAMETERS["fee_bps"])), step=0.5,
            help="每次买入或卖出各计一次成本。1 个基点等于 0.01%。",
            key=f"fee_bps_{form_key}",
        )
        save_col, delete_col = st.columns(2)
        save = save_col.form_submit_button("保存配置", type="primary", width="stretch")
        delete = delete_col.form_submit_button("删除此项", width="stretch") if form_key != "new" else False

    if delete:
        try:
            delete_watchlist_entry(int(entry["id"]))
        except Exception as exc:
            st.error(f"无法删除：{exc}")
        else:
            mark_pending()
            message = describe(ensure_synced())
            display_name = entry.get("name") or entry["symbol"]
            st.session_state["watchlist_notice"] = f"已删除 {display_name}（{entry['symbol']}）的配置。{message}"
            st.rerun()
    if save:
        try:
            save_watchlist_entry(
                {
                    **entry,
                    **values,
                    "symbol": symbol,
                    "name": name,
                    "instrument_type": instrument_type,
                    "enabled": enabled,
                    "strategy_id": strategy_id,
                    "adjust": {"前复权": "qfq", "不复权": "", "后复权": "hfq"}[adjust_label],
                    "fee_bps": fee_bps,
                }
            )
        except ValueError as exc:
            st.error(f"无法保存：{exc}")
        except Exception as exc:
            st.error(f"无法保存：{exc}")
        else:
            mark_pending()
            message = describe(ensure_synced())
            st.success(f"配置已保存。{message}")
            st.rerun()


def _parameter_inputs(strategy_id: str, entry: dict[str, Any], form_key: str) -> dict[str, Any]:
    if strategy_id == "ma_crossover":
        first, second = st.columns(2)
        return {
            "short_window": first.number_input(
                "短期均线（日）", min_value=2, max_value=120,
                value=int(entry.get("short_window", DEFAULT_PARAMETERS["short_window"])),
                help="计算短期平均收盘价的交易日数量。越短越敏感，也越容易受短期波动干扰。",
                key=f"short_{form_key}",
            ),
            "long_window": second.number_input(
                "长期均线（日）", min_value=5, max_value=250,
                value=int(entry.get("long_window", DEFAULT_PARAMETERS["long_window"])),
                help="计算长期平均收盘价的交易日数量，必须大于短期均线。越长确认越慢但通常更稳定。",
                key=f"long_{form_key}",
            ),
        }
    first, second = st.columns(2)
    third, fourth = st.columns(2)
    return {
        "breakout_window": first.number_input(
            "突破窗口（日）", min_value=2, max_value=250,
            value=int(entry.get("breakout_window", DEFAULT_PARAMETERS["breakout_window"])),
            help="当天收盘价必须突破此前这个周期的最高收盘价，才可能形成入场信号。",
            key=f"breakout_{form_key}",
        ),
        "volume_window": second.number_input(
            "平均成交量窗口（日）", min_value=2, max_value=120,
            value=int(entry.get("volume_window", DEFAULT_PARAMETERS["volume_window"])),
            help="用此前这个周期的平均成交量作为放量基准。",
            key=f"volume_{form_key}",
        ),
        "volume_multiple": third.number_input(
            "放量倍数", min_value=0.5, max_value=5.0,
            value=float(entry.get("volume_multiple", DEFAULT_PARAMETERS["volume_multiple"])), step=0.1,
            help="当天成交量至少达到平均成交量的多少倍。例如 1.5 表示至少增加 50%。",
            key=f"multiple_{form_key}",
        ),
        "exit_window": fourth.number_input(
            "退出均线（日）", min_value=2, max_value=120,
            value=int(entry.get("exit_window", DEFAULT_PARAMETERS["exit_window"])),
            help="持仓后，收盘价跌破这条均线时产生离场信号。",
            key=f"exit_{form_key}",
        ),
    }
