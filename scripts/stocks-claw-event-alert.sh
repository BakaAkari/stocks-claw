#!/usr/bin/env bash
set -eu
cd /mnt/user/code-project/stocks-claw || exit 1

# Check for economic calendar event triggers.
# Only acts when an event is in its post-release window.
event_json=$(uv run python -m stocks.adapters.cli --check-event-triggers 2>/dev/null || echo '{"triggered":[]}')
triggered=$(echo "$event_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('triggered',[])))" 2>/dev/null || echo "0")

if [ "$triggered" -eq 0 ]; then
    # No events — stay silent.
    exit 0
fi

# Event triggered! Run intelligence harvest + brief + push.
echo "[event-alert] $triggered event(s) triggered — running intelligence harvest"

# Extract event names for the alert header
event_names=$(echo "$event_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
names = [t['name'] for t in d.get('triggered', [])]
print(', '.join(names))
" 2>/dev/null || echo "Unknown event")

echo "**事件驱动情报 · $event_names**"
echo ""

uv run python -m stocks.adapters.cli --scheduled-run-session global_intelligence_watch --force --output json >/dev/null 2>&1

python3 ./scripts/intelligence_brief.py
