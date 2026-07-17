#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw-trust-t1}"
cd "$REPO_ROOT"
exec .venv/bin/python scripts/signal_settlement.py
