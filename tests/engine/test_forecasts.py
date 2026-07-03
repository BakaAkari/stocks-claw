from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from stocks.domain.models import ForecastRecord, Instrument
from stocks.engine.forecasts import settle_due_forecasts, summarize_forecasts
from stocks.engine.history_cache import HistoryCache


def _forecast(
    *,
    id: str,
    comparator: str,
    level: float,
    deadline: str = "2026-07-02",
    target: str = "a:588000",
) -> ForecastRecord:
    return ForecastRecord(
        id=id,
        created_at="2026-07-01T00:00:00+00:00",
        statement=f"{target} 收盘 {comparator} {level}",
        target=target,
        metric="close",
        comparator=comparator,
        level=level,
        deadline=deadline,
        confidence="medium",
        status="open",
    )


def _history(closes: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "timestamp": pd.Timestamp(day, tz="UTC"),
            "code": "588000",
            "name": "科创50ETF",
            "market": "a",
            "price": close,
            "open_price": close,
            "high": close,
            "low": close,
            "prev_close": close,
            "volume_lot": 100,
            "data_source": "provider",
        }
        for day, close in closes
    ])


async def test_settle_due_forecasts_hit_miss_unresolved_and_manual(tmp_path):
    instrument = Instrument("588000", "科创50ETF", "a")
    cache = HistoryCache(base_dir=str(tmp_path / "history"), ttl=86400)
    await cache.warm(instrument, _history([
        ("2026-07-01", 0.95),
        ("2026-07-02", 1.2),
    ]))
    manual = ForecastRecord.create(
        statement="市场风险偏好转弱，需要人工复盘。",
        comparator="below",
        deadline="2026-07-02",
        confidence="low",
    )
    forecasts = [
        _forecast(id="hit", comparator="above", level=1.0).to_dict(),
        _forecast(id="miss", comparator="below", level=1.0).to_dict(),
        _forecast(id="missing", comparator="above", level=1.0, target="a:000001").to_dict(),
        manual.to_dict(),
    ]

    reviewed, changed = await settle_due_forecasts(
        forecasts,
        watchlist=[instrument],
        history_cache=cache,
        as_of=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    statuses = {item["id"]: item["status"] for item in reviewed}
    assert statuses == {
        "hit": "hit",
        "miss": "miss",
        manual.id: "manual",
        "missing": "unresolved",
    }
    assert {item.id for item in changed} == {"hit", "miss", "missing"}
    missing = next(item for item in reviewed if item["id"] == "missing")
    assert missing["resolution_note"] == "target_not_in_watchlist"


async def test_forecast_waits_until_deadline(tmp_path):
    instrument = Instrument("588000", "科创50ETF", "a")
    cache = HistoryCache(base_dir=str(tmp_path / "history"), ttl=86400)
    await cache.warm(instrument, _history([("2026-07-02", 1.2)]))
    forecast = _forecast(id="future", comparator="above", level=1.0, deadline="2026-07-10")

    reviewed, changed = await settle_due_forecasts(
        [forecast.to_dict()],
        watchlist=[instrument],
        history_cache=cache,
        as_of=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert reviewed[0]["status"] == "open"
    assert changed == []


def test_forecast_summary_uses_sample_insufficient_semantics():
    records = [
        ForecastRecord(
            id=f"hit-{index}",
            created_at=f"2026-07-0{index + 1}T00:00:00+00:00",
            statement="样本内命中",
            target="a:588000",
            metric="close",
            comparator="above",
            level=1.0,
            deadline=f"2026-07-0{index + 1}",
            confidence="medium",
            status="hit",
            resolved_at=f"2026-07-0{index + 1}T08:00:00+00:00",
        ).to_dict()
        for index in range(9)
    ]

    summary = summarize_forecasts(records)

    assert summary["sample_count"] == 9
    assert summary["hit_count"] == 9
    assert summary["hit_rate"] is None
    assert summary["sample_note"] == "样本不足"
