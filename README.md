# A股策略实验室

一个使用 AKShare 行情数据的本地量化回测网页。首版内置双均线趋势策略，展示
K 线买卖点、策略净值、基准净值、回撤、绩效指标与交易记录。

## 启动

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

浏览器访问 `http://localhost:8501`。

## 免费云端部署（Render）

仓库根目录的 `render.yaml` 已包含 Render Web Service 配置。首次在 Render 控制台
选择 **New → Blueprint** 并连接本仓库后，平台会自动安装依赖、启动 Streamlit，并在
后续推送到 `master` 时自动更新。免费实例在 15 分钟无访问后会休眠；再次访问时通常
需要约一分钟唤醒。

## 跨设备协作

项目代码、开发约定和进度通过 GitHub 仓库同步：

- `AGENTS.md`：开发协作规则和工程约定。
- `PROJECT_STATUS.md`：当前功能、已解决问题和后续路线。
- `CROSS_DEVICE_SETUP.md`：Windows、Mac、Codex 和 GitHub 的完整配置步骤。

在另一台电脑首次使用时克隆仓库，之后开始工作前执行：

```bash
git pull origin master
```

## 策略规则

- 短期均线高于长期均线时持有，否则空仓。
- 当日收盘产生信号，下一交易日开始计入持仓收益，避免未来数据。
- 每次买入或卖出按设置的单边费用扣减。
- 默认使用前复权日线，便于计算连续收益。

## 说明

本项目用于策略原型和学习，不构成投资建议。回测尚未模拟滑点、涨跌停无法成交、
停牌、印花税差异和实际成交价格。
