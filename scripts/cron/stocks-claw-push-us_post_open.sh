#!/usr/bin/env bash
set -euo pipefail
cd /mnt/user/code-project/stocks-claw
exec .venv/bin/python scripts/run_llm_report.py --session us_post_open
