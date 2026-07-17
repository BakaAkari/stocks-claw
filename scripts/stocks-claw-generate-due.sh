#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw-trust-t1}"
EXPECTED_BRANCH="${STOCKS_CLAW_EXPECTED_BRANCH:-feat/decision-trust-t1}"

cd "$REPO_ROOT"
branch=$(git branch --show-current)
if [[ "$branch" != "$EXPECTED_BRANCH" ]]; then
  echo "stocks-claw generator refused: expected branch $EXPECTED_BRANCH, got $branch" >&2
  exit 40
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "stocks-claw generator refused: .venv/bin/python missing" >&2
  exit 41
fi

exec .venv/bin/python -m stocks.adapters.cli --scheduled-run-due
