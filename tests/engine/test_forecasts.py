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


# ─── Task 7: build_forecast_candidates ─────────────────────────────


def test_build_forecast_candidates_empty_when_no_thresholds():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok",
        "source_refs": [],
        "forecast_candidates": [],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_empty_when_only_prose():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok",
        "summary": "VIX可能在8月前高于25，建议关注",
        "scenarios": {
            "risk": {
                "validation": ["VIX指数持续高于25"],
            },
        },
        "source_refs": [],
        "forecast_candidates": [],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_vix_statement_deterministic():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok",
        "source_refs": [
            {"id": "src-vix", "source": "CBOE", "title": "VIX Index",
             "url": "https://example.com/vix",
             "published_at": "2026-07-17T00:00:00+00:00"},
        ],
        "forecast_candidates": [
            {
                "target": "macro:VIX",
                "metric": "close",
                "comparator": "above",
                "level": 25.0,
                "deadline": "2026-08-01",
                "confidence": "low",
                "source_ref_ids": ["src-vix"],
            },
        ],
    }
    candidates = build_forecast_candidates(outlook)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["target"] == "macro:VIX"
    assert c["metric"] == "close"
    assert c["comparator"] == "above"
    assert c["level"] == 25.0
    assert c["deadline"] == "2026-08-01"
    assert c["confidence"] == "low"
    assert c["source_ref_ids"] == ["src-vix"]
    assert c["requires_confirmation"] is True
    # Deterministic: VIX, no metric close, int level, no .0
    assert c["statement"] == "VIX 在 2026-08-01 前高于 25"


def test_build_forecast_candidates_discards_missing_target():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok", "source_refs": [],
        "forecast_candidates": [
            {"target": "", "metric": "close", "comparator": "above", "level": 25.0,
             "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-vix"]},
        ],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_discards_unknown_source(tmp_path):
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok", "source_refs": [{"id": "src-valid", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": "macro:VIX", "metric": "close", "comparator": "above", "level": 25.0,
             "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-unknown"]},
        ],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_discards_wrong_comparator():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok", "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": "macro:VIX", "metric": "close", "comparator": "gte", "level": 25.0,
             "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-v"]},
        ],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_discards_bool_level():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok", "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": "macro:VIX", "metric": "close", "comparator": "above", "level": True,
             "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-v"]},
        ],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_discards_nan_inf_level():
    import math

    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok", "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": "macro:VIX", "metric": "close", "comparator": "above", "level": math.nan,
             "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-v"]},
        ],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_discards_bad_date():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok", "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": "macro:VIX", "metric": "close", "comparator": "above", "level": 25.0,
             "deadline": "not-a-date", "confidence": "low", "source_ref_ids": ["src-v"]},
        ],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_discards_wrong_confidence():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok", "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": "macro:VIX", "metric": "close", "comparator": "above", "level": 25.0,
             "deadline": "2026-08-01", "confidence": "very_high", "source_ref_ids": ["src-v"]},
        ],
    }
    assert build_forecast_candidates(outlook) == []


def test_build_forecast_candidates_requires_confirmation_always_true():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok",
        "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {
                "target": "macro:VIX", "metric": "close", "comparator": "above", "level": 25.0,
                "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-v"],
                "requires_confirmation": False,
            },
        ],
    }
    candidates = build_forecast_candidates(outlook)
    assert len(candidates) == 1
    assert candidates[0]["requires_confirmation"] is True


def test_build_forecast_candidates_uses_explicit_statement():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok",
        "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {
                "target": "macro:VIX", "metric": "close", "comparator": "above", "level": 25.0,
                "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-v"],
                "statement": "VIX volatility index expected above 25 by August 1",
            },
        ],
    }
    candidates = build_forecast_candidates(outlook)
    assert candidates[0]["statement"] == "VIX volatility index expected above 25 by August 1"


def test_build_forecast_candidates_max_five():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok",
        "source_refs": [{"id": "src-v", "source": "X", "title": "Y", "url": "https://x.y", "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": f"macro:VIX{i}", "metric": "close", "comparator": "above", "level": float(i),
             "deadline": "2026-08-01", "confidence": "low", "source_ref_ids": ["src-v"]}
            for i in range(10)
        ],
    }
    candidates = build_forecast_candidates(outlook)
    assert len(candidates) <= 5


def test_build_forecast_candidates_is_pure_function():
    from stocks.engine.forecasts import build_forecast_candidates
    outlook = {
        "status": "ok",
        "source_refs": [{"id": "src-v", "source": "X", "title": "Y",
                         "url": "https://x.y",
                         "published_at": "2026-07-17T00:00:00+00:00"}],
        "forecast_candidates": [
            {"target": "macro:VIX", "metric": "close", "comparator": "above",
             "level": 25.0, "deadline": "2026-08-01", "confidence": "low",
             "source_ref_ids": ["src-v"]},
        ],
    }
    candidates = build_forecast_candidates(outlook)
    assert len(candidates) == 1
    assert candidates[0]["target"] == "macro:VIX"
