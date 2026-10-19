#!/usr/bin/env bash
set -eu
REPO_ROOT="${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw}"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"

# 1. Check for economic calendar event triggers first.
#    If events are in their post-release window, use --scheduled-run-due
#    which prioritizes event-triggered intelligence harvest.
event_check=$($PY -m stocks.adapters.cli --check-event-triggers 2>/dev/null || true)
has_triggers=$(echo "$event_check" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('triggered') else 'false')" 2>/dev/null || echo "false")

if [ "$has_triggers" = "true" ]; then
    echo "[intelligence-hourly] Event trigger(s) detected — running event-driven harvest"
    $PY -m stocks.adapters.cli --scheduled-run-due --force --output json >/dev/null 2>&1 || true
else
    # 2. No event triggers — standard hourly intelligence patrol.
    # set -e 下失败命令会直接退出，需在 if 中捕获才能输出失败原因
    if ! $PY -m stocks.adapters.cli --scheduled-run-session global_intelligence_watch --force --output json >/dev/null 2>&1; then
        echo "global_intelligence_watch failed"
        exit 1
    fi
fi

# 3. Generate structured brief + Feishu delivery.
"$PY" "$REPO_ROOT/scripts/intelligence_brief.py"
