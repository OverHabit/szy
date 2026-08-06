"""Streamlit interface for changing the local configurable watchlist."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from watchlist_store import (
    DEFAULT_PARAMETERS,
    delete_watchlist_entry,
    list_watchlist,
    save_watchlist_entry,
)


STRATEGIES = {
    "ma_crossover": "双均线趋势",
    "volume_breakout": "放量突破",
}


def render_watchlist_page() -> None:
    st.title("自选信号")
    st.caption("在此调整股票、策略和参数。配置会保存在当前电脑，无需编辑配置文件。")
    st.info(
        "此页保存到当前运行环境的本地数据库。云端 Streamlit 与本机各自保存一份配置；"
        "如需长期跨设备共用，下一步可接入共享数据库。"
    )

    entries = list_watchlist()
    enabled_count = sum(bool(entry["enabled"]) for entry in entries)
    st.subheader(f"已配置 {len(entries)} 项，其中启用 {enabled_count} 项")
    if entries:
        overview = pd.DataFrame(
            [
                {
                    "代码": entry["symbol"],
                    "名称": entry["name"] or "—",
                    "策略": STRATEGIES[entry["strategy_id"]],
                    "状态": "启用" if entry["enabled"] else "暂停",
                }
                for entry in entries
            ]
        )
        st.dataframe(overview, width="stretch", hide_index=True)

    with st.expander("添加股票或 ETF 策略", expanded=not entries):
        _entry_form("new", {**DEFAULT_PARAMETERS, "enabled": True, "name": "", "symbol": "", "strategy_id": "ma_crossover"})

    st.subheader("编辑已有配置")
    for entry in entries:
        display_name = entry["name"] or entry["symbol"]
        with st.expander(f"{display_name}（{entry['symbol']}）· {STRATEGIES[entry['strategy_id']]}"):
            _entry_form(str(entry["id"]), entry)

    st.caption("研究提示：参数与历史回测仅供研究参考，不构成投资建议。")


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
        delete_watchlist_entry(int(entry["id"]))
        st.rerun()
    if save:
        try:
            save_watchlist_entry(
                {
                    **entry,
                    **values,
                    "symbol": symbol,
                    "name": name,
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
            st.success("配置已保存。")
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
