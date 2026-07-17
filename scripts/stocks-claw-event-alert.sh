#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw-trust-t1}"
EXPECTED_BRANCH="${STOCKS_CLAW_EXPECTED_BRANCH:-feat/decision-trust-t1}"
cd "$REPO_ROOT"
branch=$(git branch --show-current)
if [[ "$branch" != "$EXPECTED_BRANCH" ]]; then
  echo "event alert refused: expected branch $EXPECTED_BRANCH, got $branch" >&2
  exit 40
fi
PYTHON=.venv/bin/python

event_json=$($PYTHON -m stocks.adapters.cli --check-event-triggers 2>/dev/null || echo '{"triggered":[]}')
triggered=$(printf '%s' "$event_json" | $PYTHON -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('triggered',[])))" 2>/dev/null || echo "0")
if [ "$triggered" -eq 0 ]; then exit 0; fi

event_names=$(printf '%s' "$event_json" | $PYTHON -c "import sys,json; d=json.load(sys.stdin); print(', '.join(t['name'] for t in d.get('triggered', [])))" 2>/dev/null || echo "Unknown event")
printf '**事件驱动情报 · %s**\n\n' "$event_names"
$PYTHON -m stocks.adapters.cli --scheduled-run-session global_intelligence_watch --force --output json >/dev/null 2>&1
$PYTHON ./scripts/intelligence_brief.py
