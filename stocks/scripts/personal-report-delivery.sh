#!/usr/bin/env bash
set -euo pipefail

# Personal Investment Advisor - Report Delivery Script
# Usage: bash stocks/scripts/personal-report-delivery.sh

# 自动检测项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

# LLM 配置（可通过环境变量覆盖）
export STOCKS_LLM_MODEL="${STOCKS_LLM_MODEL:-gpt-5.4}"
export STOCKS_FALLBACK_LLM_MODEL="${STOCKS_FALLBACK_LLM_MODEL:-kimi-k2.5}"
export STOCKS_LLM_URL="${STOCKS_LLM_URL:-http://localhost:11434/v1/chat/completions}"
export STOCKS_LLM_API_KEY="${STOCKS_LLM_API_KEY:-}"

# 生成报告
python3 stocks/cli/send_llm_report.py --refresh-news --save >/tmp/personal-report-delivery.out
REPORT_CONTENT=$(cat stocks/reports/personal-latest.md)

# 输出到 stdout（用于 cron 捕获）
echo "$REPORT_CONTENT"

# 如果是手动执行（不是 cron），显示提示
echo -e "\n---\n报告已生成。如果通过 cron 运行，会自动投递到 Feishu。"
