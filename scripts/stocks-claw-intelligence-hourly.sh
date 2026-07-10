#!/usr/bin/env bash
cd /mnt/user/code-project/stocks-claw || exit 1

# Load LLM key for intelligence_brief.py
if [ -f /opt/data/.env ]; then
    export $(grep -E '^OPENAI_(COMPATIBLE_)?(API_KEY|BASE_URL)=' /opt/data/.env | xargs)
fi

# Run global_intelligence_watch session with force to get fresh data
uv run python -m stocks.adapters.cli --scheduled-run-session global_intelligence_watch --force --output json >/dev/null 2>&1
exit_code=$?

if [ $exit_code -ne 0 ]; then
    echo "global_intelligence_watch failed (exit $exit_code)"
    exit $exit_code
fi

# Generate structured brief + hourly Feishu delivery (with LLM summary)
python3 ./scripts/intelligence_brief.py
