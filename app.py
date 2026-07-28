from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from market_data import fetch_a_share_history, resolve_a_share
from quant_core import BacktestMetrics, run_ma_crossover


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


def build_price_chart(data: pd.DataFrame, short: int, long: int) -> go.Figure:
    fig = go.Figure()
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
        )
    )
    fig.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["ma_short"],
            name=f"MA{short}",
            line={"color": "#d3902f", "width": 1.6},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["ma_long"],
            name=f"MA{long}",
            line={"color": "#315a87", "width": 1.6},
        )
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
        )
    )
    fig.add_trace(
        go.Scatter(
            x=sells["date"],
            y=sells["high"] * 1.02,
            mode="markers",
            name="卖出",
            marker={"symbol": "triangle-down", "size": 11, "color": "#176b4d"},
        )
    )
    fig.update_layout(
        height=520,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "x": 0},
        xaxis_rangeslider_visible=False,
        yaxis_title="价格（元）",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#e7ece8")
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
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.03, "x": 0},
    )
    fig.update_yaxes(title_text="累计净值", gridcolor="#e7ece8", row=1, col=1)
    fig.update_yaxes(title_text="回撤", tickformat=".0%", gridcolor="#e7ece8", row=2, col=1)
    fig.update_xaxes(showgrid=False)
    return fig


def show_metrics(metrics: BacktestMetrics) -> None:
    columns = st.columns(7)
    values = [
        ("策略收益", pct(metrics.total_return)),
        ("买入持有", pct(metrics.benchmark_return)),
        ("年化收益", pct(metrics.annual_return)),
        ("最大回撤", pct(metrics.max_drawdown)),
        ("夏普比率", f"{metrics.sharpe:.2f}"),
        ("交易次数", str(metrics.trade_count)),
        ("胜率", f"{metrics.win_rate:.1%}"),
    ]
    for column, (label, value) in zip(columns, values):
        column.metric(label, value)


with st.sidebar:
    st.subheader("回测参数")
    stock_query = st.text_input(
        "股票代码或名称",
        value="贵州茅台",
        placeholder="例如：600519 或 贵州茅台",
    )
    start = st.date_input("开始日期", value=date(2022, 1, 1))
    end = st.date_input("结束日期", value=date.today())
    adjust_label = st.selectbox("价格处理", ["前复权", "不复权", "后复权"])
    adjust = {"前复权": "qfq", "不复权": "", "后复权": "hfq"}[adjust_label]
    short_window = st.number_input("短期均线（日）", min_value=2, max_value=120, value=10)
    long_window = st.number_input("长期均线（日）", min_value=5, max_value=250, value=30)
    fee_bps = st.number_input(
        "单边费用（基点）", min_value=0.0, max_value=50.0, value=3.0, step=0.5
    )
    run_clicked = st.button("运行回测", type="primary", width="stretch")
    st.caption("行情由 AKShare 提供；信号按收盘价计算，下一交易日生效。")

st.title("A股策略实验室")
st.caption("双均线趋势策略 · AKShare 日线数据 · 仅用于研究与学习")

if not run_clicked:
    st.info("在左侧调整参数，然后点击“运行回测”。")
    st.stop()

try:
    if int(short_window) >= int(long_window):
        raise ValueError("短期均线必须小于长期均线")
    with st.spinner("正在获取行情并计算策略…"):
        symbol, stock_name = resolve_a_share(stock_query)
        prices = load_prices(symbol, start, end, adjust)
        data_source = prices.attrs.get("source", "AKShare")
        using_stale_cache = bool(prices.attrs.get("stale", False))
        result, metrics, trades = run_ma_crossover(
            prices,
            short_window=int(short_window),
            long_window=int(long_window),
            fee_bps=float(fee_bps),
        )
except Exception as exc:
    st.error(f"回测失败：{exc}")
    st.stop()

display_name = (
    symbol if stock_name == symbol else f"{stock_name}（{symbol}）"
)
st.subheader(f"{display_name} 回测概览")
if using_stale_cache:
    st.warning(f"在线行情源暂时不可用，当前使用{data_source}。")
else:
    st.caption(f"行情数据源：{data_source}")
show_metrics(metrics)

price_tab, performance_tab, trades_tab, data_tab = st.tabs(
    ["行情与信号", "收益与回撤", "交易记录", "每日数据"]
)

with price_tab:
    st.plotly_chart(
        build_price_chart(result, int(short_window), int(long_window)),
        width="stretch",
        config={"displaylogo": False, "scrollZoom": True},
    )

with performance_tab:
    st.plotly_chart(
        build_performance_chart(result),
        width="stretch",
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
    daily = result[
        [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "ma_short",
            "ma_long",
            "position",
            "strategy_return",
            "strategy_equity",
        ]
    ].copy()
    daily.columns = [
        "日期",
        "开盘",
        "最高",
        "最低",
        "收盘",
        "成交量",
        f"MA{int(short_window)}",
        f"MA{int(long_window)}",
        "持仓",
        "策略日收益",
        "策略净值",
    ]
    st.dataframe(daily.iloc[::-1], width="stretch", hide_index=True)

st.caption(
    "风险提示：该模型未考虑滑点、涨跌停无法成交、停牌和税费差异，历史回测不代表未来表现。"
)
