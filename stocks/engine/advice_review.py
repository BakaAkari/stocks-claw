"""建议表现回看。

只并列建议方向与历史价格事实，不判断建议对错。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
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
        trigger_review = []
        for trigger in advice.get("triggers", []):
            trigger_review.append(
                await _review_trigger(
                    trigger,
                    created_at=advice.get("created_at"),
                    watchlist_by_key=watchlist_by_key,
                    history_cache=history_cache,
                )
            )
        enriched["trigger_review"] = trigger_review
        enriched_records.append(enriched)
    return enriched_records


def attach_execution_review(
    advice_records: list[dict],
    execution_records: list[dict],
) -> list[dict]:
    """为建议 actions 附加执行对照；只按 advice_id + target 精确匹配。"""
    if not advice_records:
        return []
    if not execution_records:
        return [_with_unknown_execution_review(advice) for advice in advice_records]

    by_key: dict[tuple[str, str], dict] = {}
    for record in execution_records:
        advice_id = record.get("advice_id")
        target = record.get("target")
        if isinstance(advice_id, str) and isinstance(target, str):
            by_key.setdefault((advice_id, target), record)

    reviewed: list[dict] = []
    for advice in advice_records:
        enriched = deepcopy(advice)
        advice_id = str(advice.get("id") or advice.get("created_at") or "")
        execution_review = []
        for action in advice.get("actions", []):
            target = action.get("target")
            record = by_key.get((advice_id, target))
            execution_review.append(
                _execution_review_item(action, record)
            )
        enriched["execution_review"] = execution_review
        reviewed.append(enriched)
    return reviewed


def _with_unknown_execution_review(advice: dict) -> dict:
    enriched = deepcopy(advice)
    enriched["execution_review"] = [
        _execution_review_item(action, None)
        for action in advice.get("actions", [])
    ]
    return enriched


def _execution_review_item(action: dict, record: Optional[dict]) -> dict:
    item = {
        "target": action.get("target"),
        "recommended_action": action.get("action"),
        "status": "unknown",
    }
    if record is None:
        return item
    execution_action = record.get("action")
    if execution_action == "none":
        item["status"] = "not_executed"
    elif record.get("extent") == "full":
        item["status"] = "executed"
    elif record.get("extent") == "partial":
        item["status"] = "partial"
    item["execution"] = record
    return item


async def _review_trigger(
    trigger: dict,
    *,
    created_at: Optional[str],
    watchlist_by_key: dict[str, Instrument],
    history_cache: Optional[HistoryCache],
) -> dict:
    """按建议日后的收盘序列核对单个触发条件。"""
    key = str(trigger.get("instrument", ""))
    trigger_type = trigger.get("type")
    level = trigger.get("level")
    base = {
        "instrument": key,
        "type": trigger_type,
        "level": level,
        "action": trigger.get("action"),
        "invalidation": trigger.get("invalidation"),
    }
    instrument = watchlist_by_key.get(key)
    if instrument is None:
        return {**base, "status": "no_data", "reason": "instrument_not_in_watchlist"}
    if history_cache is None:
        return {**base, "status": "no_data", "reason": "history_cache_unavailable"}
    advice_at = _advice_datetime(created_at)
    if advice_at is None:
        return {**base, "status": "no_data", "reason": "invalid_created_at"}

    history = await history_cache.get_history(instrument, lookback_bars=500)
    if history.empty:
        return {**base, "status": "no_data", "reason": "missing_history"}
    frame = _clean_history_since(history, advice_at)
    if len(frame) < 2:
        return {**base, "status": "no_data", "reason": "insufficient_history"}

    prices = frame["price"].astype(float)
    start_price = float(prices.iloc[0])
    latest_price = float(prices.iloc[-1])
    if start_price <= 0:
        return {**base, "status": "no_data", "reason": "invalid_start_price"}
    observed = {
        "basis": "close",
        "start_at": frame.iloc[0]["timestamp"].isoformat(),
        "latest_at": frame.iloc[-1]["timestamp"].isoformat(),
        "start_price": start_price,
        "latest_price": latest_price,
        "max_price": float(prices.max()),
        "min_price": float(prices.min()),
        "pct_change": round((latest_price / start_price - 1.0) * 100, 4),
    }

    fired = False
    if trigger_type in ("price_above", "price_below"):
        for previous, current in zip(prices.iloc[:-1], prices.iloc[1:]):
            if trigger_type == "price_above" and previous < level <= current:
                fired = True
                break
            if trigger_type == "price_below" and previous > level >= current:
                fired = True
                break
    elif trigger_type in ("pct_change_above", "pct_change_below"):
        changes = (prices.iloc[1:] / start_price - 1.0) * 100
        if trigger_type == "pct_change_above":
            fired = bool((changes >= level).any())
        else:
            fired = bool((changes <= level).any())
    else:
        return {**base, "status": "no_data", "reason": "unsupported_trigger_type"}

    return {
        **base,
        "status": "fired" if fired else "not_fired",
        "observed": observed,
    }


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
    advice_at = _advice_datetime(created_at)
    if advice_at is None:
        return {**base, "status": "no_data", "reason": "invalid_created_at"}

    history = await history_cache.get_history(instrument, lookback_bars=500)
    if history.empty:
        return {**base, "status": "no_data", "reason": "missing_history"}
    frame = _clean_history_since(history, advice_at)
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


def _clean_history_since(history: pd.DataFrame, advice_at: datetime) -> pd.DataFrame:
    frame = history.copy()
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], format="ISO8601", utc=True, errors="coerce"
    )
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "price"]).sort_values("timestamp")
    return frame[frame["timestamp"] >= advice_at]


def _advice_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    return timestamp.tz_convert(timezone.utc).to_pydatetime()


def _instrument_key(instrument: Instrument) -> str:
    return f"{instrument.market}:{instrument.code}"
