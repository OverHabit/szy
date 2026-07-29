from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from market_data import fetch_a_share_history, resolve_a_share
from quant_core import BacktestMetrics, run_ma_crossover, run_volume_breakout


STRATEGIES = {
    "ma_crossover": {
        "name": "双均线趋势",
        "description": """
**交易规则**：短期均线高于长期均线时发出持有信号，反之离场。信号在收盘后生成，
下一交易日开始计入收益。

**适用环境**：趋势清晰、持续上涨或下跌的市场。

**主要风险**：横盘震荡时容易反复进出；均线本身是滞后指标，可能错过趋势初段。
""",
    },
    "volume_breakout": {
        "name": "放量突破",
        "description": """
**交易规则**：收盘价突破过去 N 日收盘高点，且当日成交量至少为过去平均成交量的指定倍数时，
发出买入信号；收盘价跌破退出均线时离场。信号在收盘后生成，下一交易日开始计入收益。

**适用环境**：价格突破整理区间且资金参与度提高的行情。

**主要风险**：放量突破可能是假突破；成交量异常也可能来自消息冲击，策略未模拟涨跌停、
滑点与无法成交的情形。
""",
    },
}

METRIC_HELP = {
    "策略收益": "策略在整个回测区间的累计收益，已扣除设置的买卖费用。",
    "买入持有": "在回测首日买入并持有到最后一日的累计收益，用作策略基准；当前未扣除交易费用。",
    "年化收益": "将策略累计收益按每年约 252 个交易日折算的复合年化收益，便于比较不同长度的回测区间。",
    "最大回撤": "策略净值从任一历史高点到随后低点的最大跌幅。数值越接近 0，历史回撤越小。",
    "夏普比率": "策略日收益的年化风险调整收益指标；当前以 0 为无风险收益率假设。数值越高通常表示单位波动获得的收益越多。",
    "交易次数": "按每段持仓周期计数；已平仓交易和截至回测最后一日仍持有的交易各计一次。",
    "胜率": "已平仓交易中，扣除买卖费用后收益为正的交易占比；未平仓交易不计入胜率。",
}


st.set_page_config(
    page_title="A股策略实验室",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --ink: #17211b;
        --muted: #69736c;
        --line: #dce3de;
        --paper: #f7f9f7;
        --green: #176b4d;
        --red: #c84a40;
    }
    .stApp { background: var(--paper); color: var(--ink); }
    [data-testid="stSidebar"] {
        background: #eef2ef;
        border-right: 1px solid var(--line);
    }
    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 14px 16px;
        min-height: 104px;
    }
    [data-testid="stMetricLabel"] { color: var(--muted); }
    [data-testid="stMetricValue"] { color: var(--ink); }
    .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    h1, h2, h3 { letter-spacing: 0 !important; color: var(--ink); }
    h1 { font-size: 2rem !important; }
    div[data-testid="stButton"] button {
        border-radius: 6px;
        border: 1px solid var(--green);
        background: var(--green);
        color: white;
        font-weight: 600;
    }
    div[data-testid="stButton"] button:hover {
        border-color: #0d5139;
        background: #0d5139;
        color: white;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=3600, show_spinner=False)
def load_prices(symbol: str, start: date, end: date, adjust: str) -> pd.DataFrame:
    return fetch_a_share_history(symbol, start, end, adjust)


def pct(value: float) -> str:
    return f"{value:+.2%}"


def chart_range(data: pd.DataFrame) -> list[pd.Timestamp]:
    """Focus charts on the most recent year while retaining all history."""
    dates = pd.to_datetime(data["date"])
    latest = dates.max()
    earliest = dates.min()
    return [max(earliest, latest - pd.DateOffset(years=1)), latest]


def chart_range_selector() -> dict[str, object]:
    return {
        "buttons": [
            {"count": 3, "label": "3个月", "step": "month", "stepmode": "backward"},
            {"count": 6, "label": "6个月", "step": "month", "stepmode": "backward"},
            {"count": 1, "label": "1年", "step": "year", "stepmode": "backward"},
            {"count": 3, "label": "3年", "step": "year", "stepmode": "backward"},
            {"label": "全部", "step": "all"},
        ],
        "x": 0,
        "y": 1.13,
        "xanchor": "left",
        "yanchor": "top",
    }


def build_price_chart(
    data: pd.DataFrame,
    strategy_id: str,
    *,
    short_window: int | None = None,
    long_window: int | None = None,
    breakout_window: int | None = None,
    exit_window: int | None = None,
) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.78, 0.22],
        vertical_spacing=0.04,
    )
    fig.add_trace(
        go.Candlestick(
            x=data["date"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            name="K线",
            increasing_line_color="#c84a40",
            decreasing_line_color="#176b4d",
        ),
        row=1,
        col=1,
    )
    if strategy_id == "ma_crossover":
        fig.add_trace(
            go.Scatter(
                x=data["date"],
                y=data["ma_short"],
                name=f"MA{short_window}",
                line={"color": "#d3902f", "width": 1.6},
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=data["date"],
                y=data["ma_long"],
                name=f"MA{long_window}",
                line={"color": "#315a87", "width": 1.6},
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=data["date"],
                y=data["breakout_high"],
                name=f"{breakout_window}日突破线",
                line={"color": "#d3902f", "width": 1.4, "dash": "dot"},
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=data["date"],
                y=data["exit_ma"],
                name=f"MA{exit_window} 退出线",
                line={"color": "#315a87", "width": 1.6},
            ),
            row=1,
            col=1,
        )

    buys = data[data["trade"] == 1]
    sells = data[data["trade"] == -1]
    fig.add_trace(
        go.Scatter(
            x=buys["date"],
            y=buys["low"] * 0.98,
            mode="markers",
            name="买入",
            marker={"symbol": "triangle-up", "size": 11, "color": "#c84a40"},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=sells["date"],
            y=sells["high"] * 1.02,
            mode="markers",
            name="卖出",
            marker={"symbol": "triangle-down", "size": 11, "color": "#176b4d"},
        ),
        row=1,
        col=1,
    )
    volume_colors = [
        "#c84a40" if close >= open_ else "#176b4d"
        for open_, close in zip(data["open"], data["close"])
    ]
    fig.add_trace(
        go.Bar(
            x=data["date"],
            y=data["volume"],
            name="成交量",
            marker_color=volume_colors,
            opacity=0.65,
        ),
        row=2,
        col=1,
    )
    if strategy_id == "volume_breakout":
        fig.add_trace(
            go.Scatter(
                x=data["date"],
                y=data["volume_ma"],
                name="平均成交量",
                line={"color": "#315a87", "width": 1.4},
            ),
            row=2,
            col=1,
        )
    fig.update_layout(
        height=640,
        margin={"l": 10, "r": 10, "t": 54, "b": 10},
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "x": 0},
    )
    fig.update_xaxes(
        range=chart_range(data),
        rangeslider_visible=False,
        showgrid=False,
    )
    fig.update_xaxes(rangeselector=chart_range_selector(), row=2, col=1)
    fig.update_yaxes(title_text="价格（元）", gridcolor="#e7ece8", automargin=True, row=1, col=1)
    fig.update_yaxes(title_text="成交量", gridcolor="#e7ece8", automargin=True, row=2, col=1)
    return fig


def build_performance_chart(data: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.07,
    )
    fig.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["strategy_equity"],
            name="策略净值",
            line={"color": "#176b4d", "width": 2.2},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["benchmark_equity"],
            name="买入持有",
            line={"color": "#69736c", "width": 1.5, "dash": "dot"},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["drawdown"],
            name="策略回撤",
            fill="tozeroy",
            line={"color": "#c84a40", "width": 1},
        ),
        row=2,
        col=1,
    )
    fig.update_layout(
        height=480,
        margin={"l": 10, "r": 10, "t": 54, "b": 10},
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.03, "x": 0},
    )
    fig.update_yaxes(
        title_text="累计净值", gridcolor="#e7ece8", automargin=True, row=1, col=1
    )
    fig.update_yaxes(
        title_text="回撤", tickformat=".0%", gridcolor="#e7ece8", automargin=True, row=2, col=1
    )
    fig.update_xaxes(range=chart_range(data), showgrid=False)
    fig.update_xaxes(
        rangeselector=chart_range_selector(), row=2, col=1
    )
    return fig


def show_metrics(metrics: BacktestMetrics) -> None:
    values = [
        ("策略收益", pct(metrics.total_return)),
        ("买入持有", pct(metrics.benchmark_return)),
        ("年化收益", pct(metrics.annual_return)),
        ("最大回撤", pct(metrics.max_drawdown)),
        ("夏普比率", f"{metrics.sharpe:.2f}"),
        ("交易次数", str(metrics.trade_count)),
        ("胜率", f"{metrics.win_rate:.1%}"),
    ]
    # Seven cards in one row leave too little room for percentage values when the
    # sidebar is open.  Keep the summary readable on ordinary laptop screens.
    for column, (label, value) in zip(st.columns(4), values[:4]):
        column.metric(label, value, help=METRIC_HELP[label])
    for column, (label, value) in zip(st.columns(3), values[4:]):
        column.metric(label, value, help=METRIC_HELP[label])


with st.sidebar:
    st.subheader("回测参数")
    strategy_id = st.selectbox(
        "策略",
        options=list(STRATEGIES),
        format_func=lambda key: STRATEGIES[key]["name"],
        help="选择要回测的交易规则。不同策略使用各自的参数与买卖信号。",
    )
    with st.expander("查看当前策略说明"):
        st.markdown(STRATEGIES[strategy_id]["description"])
    stock_query = st.text_input(
        "股票代码或名称",
        value="贵州茅台",
        placeholder="例如：600519 或 贵州茅台",
        help="输入 6 位 A 股代码，或完整股票名称。",
    )
    start = st.date_input(
        "开始日期",
        value=date(2022, 1, 1),
        help="回测使用的第一天。区间越长，样本越多，但也会包含更久以前的市场环境。",
    )
    end = st.date_input(
        "结束日期",
        value=date.today(),
        help="回测使用的最后一天。日线尚未更新时，最后一个交易日可能早于此日期。",
    )
    adjust_label = st.selectbox(
        "价格处理",
        ["前复权", "不复权", "后复权"],
        help="前复权会把历史价格按除权除息因素调整，适合连续收益计算；不复权保留当时实际价格；后复权用于观察按最新价格还原后的历史走势。",
    )
    adjust = {"前复权": "qfq", "不复权": "", "后复权": "hfq"}[adjust_label]
    if strategy_id == "ma_crossover":
        short_window = st.number_input(
            "短期均线（日）",
            min_value=2,
            max_value=120,
            value=10,
            help="计算短期平均收盘价的交易日数量。数值越小，信号越灵敏，也越容易受短期波动干扰。",
        )
        long_window = st.number_input(
            "长期均线（日）",
            min_value=5,
            max_value=250,
            value=30,
            help="计算长期平均收盘价的交易日数量。必须大于短期均线；数值越大，趋势确认越慢但通常更稳定。",
        )
    else:
        breakout_window = st.number_input(
            "突破窗口（日）",
            min_value=2,
            max_value=250,
            value=20,
            help="用于计算过去最高收盘价的天数。当天收盘价必须高于此前这个窗口内的最高收盘价，才可能形成突破。",
        )
        volume_window = st.number_input(
            "平均成交量窗口（日）",
            min_value=2,
            max_value=120,
            value=20,
            help="用于计算此前平均成交量的天数。当天成交量会与这个历史均值比较，不会使用未来成交量。",
        )
        volume_multiple = st.number_input(
            "放量倍数",
            min_value=0.5,
            max_value=5.0,
            value=1.5,
            step=0.1,
            help="入场时，当日成交量至少应是此前平均成交量的多少倍。例如 1.5 表示至少放大 50%。",
        )
        exit_window = st.number_input(
            "退出均线（日）",
            min_value=2,
            max_value=120,
            value=10,
            help="持仓后用于离场的平均收盘价周期。收盘价跌破这条均线时，策略在下一交易日转为空仓。",
        )
    fee_bps = st.number_input(
        "单边费用（基点）",
        min_value=0.0,
        max_value=50.0,
        value=3.0,
        step=0.5,
        help="每次买入或卖出各扣除一次的成本。1 个基点等于 0.01%，例如 3 个基点等于 0.03%。",
    )
    with st.expander("参数说明"):
        st.markdown(
            "- **价格处理**：回测连续收益通常选择前复权；不同复权方式的价格不能直接混合比较。"
        )
        if strategy_id == "ma_crossover":
            st.markdown(
                "- **短期均线**：越短越敏感，也越容易受短期波动干扰。\n"
                "- **长期均线**：必须大于短期均线；越长通常越稳定，但趋势确认更慢。"
            )
        else:
            st.markdown(
                "- **突破窗口**：越短越敏感，越长越强调中期新高。\n"
                "- **平均成交量窗口**：用于定义正常成交量基准；过短容易受单日异常量影响。\n"
                "- **放量倍数**：越高越严格，交易次数通常更少；它不保证突破一定有效。\n"
                "- **退出均线**：越短通常离场越快，越长则更愿意承受回撤以跟随趋势。"
            )
        st.markdown(
            "- **单边费用**：买入和卖出各计算一次；当前未包含滑点、涨跌停和无法成交的影响。"
        )
    run_clicked = st.button("运行回测", type="primary", width="stretch")
    st.caption("行情由 AKShare 提供；信号按收盘价计算，下一交易日生效。")

st.title("A股策略实验室")
st.caption("可选趋势与成交量策略 · AKShare 日线数据 · 仅用于研究与学习")

if not run_clicked:
    st.info("在左侧调整参数，然后点击“运行回测”。")
    st.stop()

try:
    with st.spinner("正在获取行情并计算策略…"):
        symbol, stock_name = resolve_a_share(stock_query)
        prices = load_prices(symbol, start, end, adjust)
        data_source = prices.attrs.get("source", "AKShare")
        using_stale_cache = bool(prices.attrs.get("stale", False))
        if strategy_id == "ma_crossover":
            result, metrics, trades = run_ma_crossover(
                prices,
                short_window=int(short_window),
                long_window=int(long_window),
                fee_bps=float(fee_bps),
            )
        else:
            result, metrics, trades = run_volume_breakout(
                prices,
                breakout_window=int(breakout_window),
                volume_window=int(volume_window),
                volume_multiple=float(volume_multiple),
                exit_window=int(exit_window),
                fee_bps=float(fee_bps),
            )
except Exception as exc:
    st.error(f"回测失败：{exc}")
    st.stop()

display_name = (
    symbol if stock_name == symbol else f"{stock_name}（{symbol}）"
)
st.subheader(f"{display_name} · {STRATEGIES[strategy_id]['name']} · 回测概览")
if using_stale_cache:
    st.warning(f"在线行情源暂时不可用，当前使用{data_source}。")
else:
    st.caption(f"行情数据源：{data_source}")
show_metrics(metrics)
with st.expander("查看回测指标说明"):
    for label in METRIC_HELP:
        st.markdown(f"- **{label}**：{METRIC_HELP[label]}")

price_tab, performance_tab, trades_tab, data_tab = st.tabs(
    ["行情与信号", "收益与回撤", "交易记录", "每日数据"]
)

with price_tab:
    st.plotly_chart(
        build_price_chart(
            result,
            strategy_id,
            short_window=int(short_window) if strategy_id == "ma_crossover" else None,
            long_window=int(long_window) if strategy_id == "ma_crossover" else None,
            breakout_window=int(breakout_window) if strategy_id == "volume_breakout" else None,
            exit_window=int(exit_window) if strategy_id == "volume_breakout" else None,
        ),
        use_container_width=True,
        config={"displaylogo": False, "scrollZoom": True},
    )

with performance_tab:
    st.plotly_chart(
        build_performance_chart(result),
        use_container_width=True,
        config={"displaylogo": False},
    )

with trades_tab:
    if trades.empty:
        st.info("当前参数下没有产生交易。")
    else:
        shown_trades = trades.copy()
        shown_trades["买入日期"] = shown_trades["买入日期"].dt.strftime("%Y-%m-%d")
        shown_trades["卖出日期"] = shown_trades["卖出日期"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            shown_trades,
            width="stretch",
            hide_index=True,
            column_config={
                "买入价": st.column_config.NumberColumn(format="%.2f"),
                "卖出价": st.column_config.NumberColumn(format="%.2f"),
                "收益率": st.column_config.NumberColumn(format="percent"),
            },
        )

with data_tab:
    columns = ["date", "open", "high", "low", "close", "volume"]
    labels = ["日期", "开盘", "最高", "最低", "收盘", "成交量"]
    if strategy_id == "ma_crossover":
        columns.extend(["ma_short", "ma_long"])
        labels.extend([f"MA{int(short_window)}", f"MA{int(long_window)}"])
    else:
        columns.extend(["breakout_high", "volume_ma", "volume_ratio", "exit_ma"])
        labels.extend(
            [
                f"{int(breakout_window)}日突破线",
                f"{int(volume_window)}日平均成交量",
                "量比",
                f"MA{int(exit_window)} 退出线",
            ]
        )
    columns.extend(["position", "strategy_return", "strategy_equity"])
    labels.extend(["持仓", "策略日收益", "策略净值"])
    daily = result[columns].copy()
    daily.columns = labels
    st.dataframe(daily.iloc[::-1], width="stretch", hide_index=True)

st.caption(
    "风险提示：该模型未考虑滑点、涨跌停无法成交、停牌和税费差异，历史回测不代表未来表现。"
)
