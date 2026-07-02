"""建议表现回看。

只并列建议方向与历史价格事实，不判断建议对错。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timezone
from typing import Optional

import pandas as pd

from stocks.domain.models import Instrument
from stocks.engine.history_cache import HistoryCache


async def attach_advice_performance(
    advice_records: list[dict],
    *,
    watchlist: list[Instrument],
    history_cache: Optional[HistoryCache],
) -> list[dict]:
    """为最近建议附加 watchlist 标的的历史表现事实。"""
    if not advice_records:
        return []

    watchlist_by_key = {
        _instrument_key(instrument): instrument
        for instrument in watchlist
    }
    enriched_records: list[dict] = []
    for advice in advice_records:
        enriched = deepcopy(advice)
        performance = []
        for item in advice.get("instruments", []):
            key = f"{item.get('market')}:{item.get('code')}"
            instrument = watchlist_by_key.get(key)
            if instrument is None:
                continue
            direction = advice.get("direction", {}).get(key, "unknown")
            performance.append(
                await _review_instrument(
                    instrument,
                    direction=direction,
                    created_at=advice.get("created_at"),
                    history_cache=history_cache,
                )
            )
        enriched["performance"] = performance
        enriched_records.append(enriched)
    return enriched_records


async def _review_instrument(
    instrument: Instrument,
    *,
    direction: str,
    created_at: Optional[str],
    history_cache: Optional[HistoryCache],
) -> dict:
    base = {
        "instrument": instrument.to_dict(),
        "direction": direction,
    }
    if history_cache is None:
        return {**base, "status": "no_data", "reason": "history_cache_unavailable"}
    advice_date = _advice_date(created_at)
    if advice_date is None:
        return {**base, "status": "no_data", "reason": "invalid_created_at"}

    history = await history_cache.get_history(instrument, lookback_bars=500)
    if history.empty:
        return {**base, "status": "no_data", "reason": "missing_history"}

    frame = history.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="ISO8601", utc=True)
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "price"]).sort_values("timestamp")
    frame = frame[frame["timestamp"].dt.date >= advice_date]
    if len(frame) < 2:
        return {**base, "status": "no_data", "reason": "insufficient_history"}

    start = frame.iloc[0]
    latest = frame.iloc[-1]
    start_price = float(start["price"])
    latest_price = float(latest["price"])
    if start_price <= 0:
        return {**base, "status": "no_data", "reason": "invalid_start_price"}
    pct_change = (latest_price / start_price - 1.0) * 100
    return {
        **base,
        "status": "ok",
        "start_at": start["timestamp"].isoformat(),
        "latest_at": latest["timestamp"].isoformat(),
        "start_price": start_price,
        "latest_price": latest_price,
        "pct_change": round(pct_change, 4),
    }


def _advice_date(value: Optional[str]):
    if not value:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    return timestamp.date()


def _instrument_key(instrument: Instrument) -> str:
    return f"{instrument.market}:{instrument.code}"
