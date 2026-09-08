# A股策略实验室

个人使用的 Streamlit 策略研究工具，支持 A 股和 ETF 回测、自选信号与本地收盘报告。

- 在线网页：https://9s5ky5xuwk4ohrjj5sdk8t.streamlit.app
- GitHub：https://github.com/OverHabit/szy
- 仅用于研究，不构成投资建议，不自动下单。

## 主要功能

- 双均线趋势、放量突破两种策略。
- K 线、成交量、买卖点、收益、回撤、指标和交易记录。
- 页面内解释策略、参数和绩效指标。
- 自选股与 ETF 配置，可一键运行全部启用项目。
- 正式日线优先；收盘后日线尚未更新时，用腾讯实时收盘行情临时计算并标注“预估”和报价时刻。
- 行情失败时自动尝试备用来源及本地缓存。

## 本地启动

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

浏览器访问 `http://localhost:8501`。Windows 也可以运行 `start_app.bat`。

自选配置保存在当前运行环境的 `.data/watchlist.db`，不会上传 GitHub，也不会在本机与在线网页之间自动同步。

## WorkBuddy 收盘邮件

当前只保留本地 WorkBuddy 流程，暂不做云端定时推送：

1. WorkBuddy 在工作日收盘后调用 `run_daily_signal.sh`。
2. 脚本读取本机自选配置并运行一次，输出报告，同时保存到 `.data/reports/日期.txt`。
3. WorkBuddy 读取标准输出，通过已连接的邮箱发送。

脚本本身不含邮箱地址、授权码或发信功能。它不会等待正式日线：若当日日线尚未发布，会使用 15:00 后的实时收盘价预估；实时行情也不可用时会明确标注旧日线或数据异常。

手动验证：

```bash
bash run_daily_signal.sh
```

退出码 `0` 表示报告已生成，`10` 表示非交易日跳过，其他值表示运行失败。WorkBuddy 是否已经启用、触发时间和邮箱投递状态属于客户端配置，不保存在本仓库。

## 核心文件

| 文件 | 用途 |
|---|---|
| `app.py` | Streamlit 主页面 |
| `watchlist_ui.py` | 自选配置与一键运行 |
| `watchlist_store.py` | 本地 SQLite 配置 |
| `market_data.py` | 行情获取、回退与缓存 |
| `quant_core.py` | 策略和回测计算 |
| `daily_signal.py` | 收盘信号报告 |
| `run_daily_signal.sh` | WorkBuddy 本地调用入口 |
| `PROJECT_STATUS.md` | 当前状态与待办 |

## 回测口径

- 当日收盘产生信号，下一交易日开始计入持仓收益，避免未来数据。
- 买入和卖出分别按设置的单边费用扣减。
- 默认使用前复权日线。
- 尚未完整模拟滑点、停牌、涨跌停、税费差异和实际成交价格。

跨设备安装和 GitHub 使用见 [CROSS_DEVICE_SETUP.md](CROSS_DEVICE_SETUP.md)。
