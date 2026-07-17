#!/usr/bin/env bash
set -euo pipefail
cd /mnt/user/code-project/stocks-claw-trust-t1
exec .venv/bin/python scripts/run_push_report.py --session cn_pre_open
