#!/bin/bash
# 运行 A 股收盘策略信号，并把报告落盘到 .data/reports/<日期>.txt。
# WorkBuddy 调用此脚本后读取标准输出并发送邮件；脚本本身不保存邮箱凭据。
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
REPORT_DIR="$PROJECT_DIR/.data/reports"

if [[ ! -x "$PYTHON" ]]; then
  echo "未找到项目虚拟环境：$PYTHON" >&2
  exit 1
fi

cd "$PROJECT_DIR" || exit 1
mkdir -p "$REPORT_DIR" || exit 1

REPORT="$REPORT_DIR/$(date +%F).txt"

"$PYTHON" -W ignore daily_signal.py 2>&1 | tee "$REPORT"

exit "${PIPESTATUS[0]}"
