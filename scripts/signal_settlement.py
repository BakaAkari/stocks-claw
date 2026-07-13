#!/usr/bin/env python3
"""Settle tracked signals against current prices.

Run via cron: every 6 hours, checks unsettled signals and settles them
if their time windows (24h / 1w) have elapsed.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = "/mnt/user/code-project/stocks-claw"
sys.path.insert(0, PROJECT_ROOT)

from stocks.engine.signal_tracker import SignalTracker
from stocks.engine.intelligence_harvester import IntelligenceHarvester


def main():
    tracker_dir = f"{PROJECT_ROOT}/.local/signal_tracker"
    tracker = SignalTracker(tracker_dir)
    now = datetime.now(timezone.utc)

    # Try to get current prices from the harvester
    try:
        harvester = IntelligenceHarvester(max_items_per_source=1)
        # We only need quotes, not full harvest
    except Exception:
        print("[signal_settlement] Cannot create harvester — prices unavailable")
        sys.exit(1)

    settled = {"24h": 0, "1w": 0}

    for window, delta in [("24h", timedelta(hours=24)), ("1w", timedelta(days=7))]:
        unsettled = tracker.unsettled(window)
        for sig in unsettled:
            cutoff = sig.generated_at + delta
            if now < cutoff:
                continue  # Not due yet

            # Get current price — use harvester's quote fetch
            if not sig.symbol:
                continue

            try:
                # Quick price fetch for the symbol
                import asyncio
                async def get_price():
                    try:
                        result = await harvester._fetch_quotes()
                        return result
                    except Exception:
                        return {}
                quotes = asyncio.run(get_price())
            except Exception:
                quotes = {}

            current_price = None
            if sig.symbol in quotes:
                q = quotes[sig.symbol]
                if isinstance(q, dict):
                    current_price = q.get("price")

            if current_price is None:
                continue

            tracker.settle(sig, window, current_price, now=now)
            settled[window] += 1
            print(f"[signal_settlement] Settled {sig.signal_id} ({window}): "
                  f"entry={sig.generation_price} exit={current_price} "
                  f"correct={sig.correct_24h if window == '24h' else sig.correct_1w}")

    # Print summary
    perf = tracker.performance()
    print(f"[signal_settlement] Done. 24h={settled['24h']} 1w={settled['1w']}. "
          f"Win rate: 24h={perf.get('win_rate_24h','?')} 1w={perf.get('win_rate_1w','?')}")


if __name__ == "__main__":
    main()
