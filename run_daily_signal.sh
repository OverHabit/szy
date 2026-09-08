#!/bin/bash
# 运行 A 股收盘策略信号，并把报告落盘到 .data/reports/<日期>.txt。
# 15:05 各数据源普遍还没有当日正式日线，因此只跑一轮：能拿到正式日线的用正式日线，
# 拿不到的用 15:00 后的实时收盘价预估，并在报告中逐条标注。
# 退出码：0 = 需要发送邮件；10 = 跳过发送（非交易日）；其他 = 执行失败。
set -uo pipefail

PROJECT_DIR="/Users/szy/Documents/量化"
PYTHON="$PROJECT_DIR/.venv/bin/python"
REPORT_DIR="$PROJECT_DIR/.data/reports"

cd "$PROJECT_DIR" || exit 1
mkdir -p "$REPORT_DIR" || exit 1

REPORT="$REPORT_DIR/$(date +%F).txt"

"$PYTHON" daily_signal.py \
  --no-wait \
  --on-timeout send \
  "$@" 2>&1 | tee "$REPORT"

exit "${PIPESTATUS[0]}"
