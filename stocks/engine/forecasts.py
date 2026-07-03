"""预测台账结算与摘要。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd

from stocks.domain.models import ForecastRecord, Instrument
from stocks.engine.history_cache import HistoryCache


async def settle_due_forecasts(
    forecast_records: list[dict],
    *,
    watchlist: list[Instrument],
    history_cache: Optional[HistoryCache],
    as_of: Optional[datetime] = None,
) -> tuple[list[dict], list[ForecastRecord]]:
    """结算已到期的 open 预测，返回上下文记录与需写回的记录。"""
    if not forecast_records:
        return [], []

    now = as_of or datetime.now(timezone.utc)
    target_by_key = {f"{instrument.market}:{instrument.code}": instrument for instrument in watchlist}
    reviewed: list[dict] = []
    changed: list[ForecastRecord] = []

    for item in forecast_records:
        record = ForecastRecord.from_dict(item)
        settled = await _settle_one(
            record,
            target_by_key=target_by_key,
            history_cache=history_cache,
            as_of=now,
        )
        reviewed.append(settled.to_dict())
        if settled != record:
            changed.append(settled)

    return reviewed, changed


def summarize_forecasts(forecast_records: list[dict]) -> dict:
    """生成预测台账摘要；hit/miss 样本不足 10 条时不输出命中率。"""
    records = [ForecastRecord.from_dict(item).to_dict() for item in forecast_records]
    open_records = [item for item in records if item["status"] == "open"]
    hit_miss = [item for item in records if item["status"] in {"hit", "miss"}]
    hit_count = len([item for item in hit_miss if item["status"] == "hit"])
    sample_count = len(hit_miss)
    hit_rate = round(hit_count / sample_count, 4) if sample_count >= 10 else None
    recent_settlements = [
        item
        for item in records
        if item["status"] in {"hit", "miss", "unresolved"}
    ]
    recent_settlements.sort(
        key=lambda item: item.get("resolved_at") or item.get("created_at") or "",
        reverse=True,
    )
    return {
        "open_count": len(open_records),
        "sample_count": sample_count,
        "hit_count": hit_count,
        "hit_rate": hit_rate,
        "sample_note": "样本不足" if sample_count < 10 else "ok",
        "recent_settlements": recent_settlements[:5],
        "recent_records": records[:10],
    }


async def _settle_one(
    record: ForecastRecord,
    *,
    target_by_key: dict[str, Instrument],
    history_cache: Optional[HistoryCache],
    as_of: datetime,
) -> ForecastRecord:
    if record.status != "open":
        return record
    deadline = date.fromisoformat(record.deadline)
    if as_of.date() < deadline:
        return record
    if record.target is None or record.level is None:
        return _resolve(record, "manual", as_of, "manual_review_required: missing target or level")
    if history_cache is None:
        return _resolve(record, "unresolved", as_of, "history_cache_unavailable")
    instrument = target_by_key.get(record.target)
    if instrument is None:
        return _resolve(record, "unresolved", as_of, "target_not_in_watchlist")

    history = await history_cache.get_history(instrument, lookback_bars=500)
    if history.empty:
        return _resolve(record, "unresolved", as_of, "missing_history")
    frame = _clean_history(history)
    if frame.empty:
        return _resolve(record, "unresolved", as_of, "missing_history")

    created_date = _created_date(record.created_at)
    frame = frame[frame["date"] <= deadline]
    if created_date is not None:
        frame = frame[frame["date"] >= created_date]
    if frame.empty:
        return _resolve(record, "unresolved", as_of, "missing_close_before_deadline")

    close_row = frame.iloc[-1]
    observed_close = float(close_row["price"])
    close_date = close_row["date"].isoformat()
    hit = observed_close > record.level if record.comparator == "above" else observed_close < record.level
    status = "hit" if hit else "miss"
    note = (
        f"deadline_close={observed_close:g} at {close_date}; "
        f"expected close {record.comparator} {record.level:g}"
    )
    return _resolve(record, status, as_of, note)


def _resolve(
    record: ForecastRecord,
    status: str,
    as_of: datetime,
    note: str,
) -> ForecastRecord:
    return replace(
        record,
        status=status,
        resolved_at=as_of.astimezone(timezone.utc).isoformat(),
        resolution_note=note,
    )


def _clean_history(history: pd.DataFrame) -> pd.DataFrame:
    frame = deepcopy(history)
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], format="ISO8601", utc=True, errors="coerce"
    )
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "price"]).sort_values("timestamp")
    if frame.empty:
        return frame
    frame["date"] = frame["timestamp"].dt.date
    return frame


def _created_date(value: str) -> Optional[date]:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    else:
        timestamp = timestamp.tz_convert(timezone.utc)
    return timestamp.date()
