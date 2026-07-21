#!/usr/bin/env bash
set -euo pipefail
cd /mnt/user/code-project/stocks-claw
exec .venv/bin/python scripts/run_push_report.py --session cn_open_watch
