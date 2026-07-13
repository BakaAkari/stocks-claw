#!/usr/bin/env bash
set -eu
cd /mnt/user/code-project/stocks-claw || exit 1

# 1. Check for economic calendar event triggers first.
#    If events are in their post-release window, use --scheduled-run-due
#    which prioritizes event-triggered intelligence harvest.
event_check=$(uv run python -m stocks.adapters.cli --check-event-triggers 2>/dev/null || true)
has_triggers=$(echo "$event_check" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('triggered') else 'false')" 2>/dev/null || echo "false")

if [ "$has_triggers" = "true" ]; then
    echo "[intelligence-hourly] Event trigger(s) detected — running event-driven harvest"
    uv run python -m stocks.adapters.cli --scheduled-run-due --force --output json >/dev/null 2>&1 || true
else
    # 2. No event triggers — standard hourly intelligence patrol.
    uv run python -m stocks.adapters.cli --scheduled-run-session global_intelligence_watch --force --output json >/dev/null 2>&1
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "global_intelligence_watch failed (exit $exit_code)"
        exit $exit_code
    fi
fi

# 3. Generate structured brief + Feishu delivery.
python3 ./scripts/intelligence_brief.py
