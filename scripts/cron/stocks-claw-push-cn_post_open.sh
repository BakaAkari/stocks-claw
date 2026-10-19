#!/usr/bin/env bash
set -euo pipefail
cd "${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw}"
exec .venv/bin/python scripts/run_llm_report.py --session cn_post_open
