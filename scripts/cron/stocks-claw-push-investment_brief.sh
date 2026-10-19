#!/usr/bin/env bash
# 投资日报：stdout 由 Hermes no-agent cron 投递；不含资产金额；LLM 失败时报告内注明不可用
set -euo pipefail
cd "${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw}"
exec .venv/bin/python -m stocks.adapters.cli --investment-brief --output text
