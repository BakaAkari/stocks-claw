"""预测台账结算与摘要。"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd

from stocks.domain.models import ForecastRecord, Instrument
from stocks.engine.history_cache import HistoryCache
from stocks.engine.outlook_helpers import collect_source_ids, is_valid_iso_date


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




# ═══════════════════════════════════════════════════════════════════
# Task 7: build_forecast_candidates
# ═══════════════════════════════════════════════════════════════════

_VALID_COMPARATORS = frozenset({"above", "below", "at_or_above", "at_or_below", "equal"})
_VALID_CONFIDENCES = frozenset({"high", "medium", "low"})


def build_forecast_candidates(outlook: dict) -> list[dict]:
    """Normalize structured forecast_candidates from a validated outlook into confirmable candidates.

    Parameters
    ----------
    outlook : dict
        A validated structured outlook with status=='ok'.

    Returns
    -------
    list[dict]
        Normalized forecast candidates (max 5), each with:
        target, metric, comparator, level, deadline, confidence,
        source_ref_ids, statement, requires_confirmation.
        Never persists anything.
    """
    if not isinstance(outlook, dict):
        return []
    if outlook.get("status") != "ok":
        return []

    raw_candidates = outlook.get("forecast_candidates")
    if not isinstance(raw_candidates, list):
        return []

    # Build set of valid source_ref IDs from outlook
    valid_source_ids = collect_source_ids(outlook)

    result: list[dict] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        normalized = _normalize_candidate(candidate, valid_source_ids)
        if normalized is not None:
            result.append(normalized)
        if len(result) >= 5:
            break

    return result


def _normalize_candidate(candidate: dict, valid_source_ids: set[str]) -> dict | None:
    """Validate and normalize a single forecast candidate; return None if invalid."""
    target = candidate.get("target")
    if not isinstance(target, str) or not target.strip():
        return None

    metric = candidate.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        return None

    comparator = candidate.get("comparator")
    if comparator not in _VALID_COMPARATORS:
        return None

    level = candidate.get("level")
    # Reject None, bool, non-numeric types
    if level is None or isinstance(level, bool):
        return None
    if not isinstance(level, (int, float)):
        return None
    if math.isnan(level) or math.isinf(level):
        return None
    level = float(level)

    deadline = candidate.get("deadline")
    if not isinstance(deadline, str) or not deadline.strip():
        return None
    # Validate ISO date format (YYYY-MM-DD)
    if not is_valid_iso_date(deadline):
        return None

    confidence = candidate.get("confidence")
    if confidence not in _VALID_CONFIDENCES:
        return None

    source_ref_ids = candidate.get("source_ref_ids")
    if not isinstance(source_ref_ids, list) or not source_ref_ids:
        return None
    # Every source_ref_id must exist in the outlook's source_refs
    if not all(isinstance(sid, str) and sid in valid_source_ids for sid in source_ref_ids):
        return None

    # Statement: explicit or auto-generated
    statement = candidate.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        statement = _generate_statement(target, metric, comparator, level, deadline)

    # requires_confirmation is always True
    requires_confirmation = True

    # Dedupe source_ref_ids preserving order, reject blank
    seen: set[str] = set()
    deduped: list[str] = []
    for sid in source_ref_ids:
        if isinstance(sid, str) and sid.strip():
            s = sid.strip()
            if s not in seen:
                seen.add(s)
                deduped.append(s)
    if not deduped:
        return None

    return {
        "target": target.strip(),
        "metric": metric.strip(),
        "comparator": comparator,
        "level": level,
        "deadline": deadline.strip(),
        "confidence": confidence,
        "source_ref_ids": deduped,
        "statement": statement.strip() if isinstance(statement, str) else statement,
        "requires_confirmation": requires_confirmation,
    }


def _generate_statement(
    target: str,
    metric: str,
    comparator: str,
    level: float,
    deadline: str,
) -> str:
    """Generate a deterministic Chinese forecast statement."""
    comparator_label = {
        "above": "高于",
        "below": "低于",
        "at_or_above": "不低于",
        "at_or_below": "不高于",
        "equal": "等于",
    }
    label = comparator_label.get(comparator, comparator)
    # macro:VIX with close metric -> show VIX, omit close
    target_stripped = target.strip()
    if target_stripped == "macro:VIX" and metric.strip() == "close":
        display = "VIX"
    else:
        display = target_stripped if not target_stripped.startswith("macro:") else target_stripped[6:]
    # Format level as int if whole number
    level_str = str(int(level)) if level == int(level) else str(level)
    return f"{display} 在 {deadline.strip()} 前{label} {level_str}"



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
