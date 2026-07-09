#!/usr/bin/env bash
cd /mnt/user/code-project/stocks-claw || exit 1

output=$(uv run python -m stocks.adapters.cli --scheduled-run-due 2>&1)
exit_code=$?

if [ $exit_code -ne 0 ]; then
    echo "stocks-claw scheduled-run-due failed (exit $exit_code):"
    echo "$output" | tail -50
    exit $exit_code
fi

result=$(echo "$output" | grep -A 20 '"success"' | tail -40)
if [ -z "$result" ]; then
    result=$(echo "$output" | tail -30)
fi

echo "$result"
